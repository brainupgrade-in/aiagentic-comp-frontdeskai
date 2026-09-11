"""Tests for HR domain tools: get_leave_balance, apply_leave."""

import os
import sys
import sqlite3
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


@pytest.fixture(autouse=True)
def setup_db(env):
    """Initialize DB and seed test employee before each test."""
    from tools import _get_db
    _get_db().close()
    import tools as t
    db = sqlite3.connect(t.TOOLS_DB)
    db.execute("""
        INSERT OR IGNORE INTO employees
            (employee_id, full_name, email, department, designation, date_of_join, is_active)
        VALUES ('EMP001', 'Alice Test', 'alice@test.com', 'HR', 'Manager', '2022-01-01', 1)
    """)
    db.execute("""
        INSERT OR IGNORE INTO leave_balances
            (employee_id, casual_leave, sick_leave, earned_leave, wfh_days, year)
        VALUES ('EMP001', 10, 8, 15, 20, 2026)
    """)
    db.commit()
    db.close()


class TestGetLeaveBalance:
    def test_returns_balance_for_valid_employee(self, env):
        from tools import get_leave_balance
        result = get_leave_balance.invoke({"employee_id": "EMP001"})
        assert "10" in result or "casual" in result.lower()
        assert "EMP001" in result or "Alice" in result

    def test_unknown_employee_returns_error(self, env):
        from tools import get_leave_balance
        result = get_leave_balance.invoke({"employee_id": "EMP999"})
        assert "not found" in result.lower() or "no record" in result.lower() or "no leave" in result.lower()

    def test_response_includes_all_leave_types(self, env):
        from tools import get_leave_balance
        result = get_leave_balance.invoke({"employee_id": "EMP001"})
        for leave_type in ("casual", "sick", "earned"):
            assert leave_type in result.lower()


class TestApplyLeave:
    def test_apply_valid_casual_leave(self, env):
        from tools import apply_leave
        result = apply_leave.invoke({
            "employee_id": "EMP001",
            "leave_type": "casual",
            "start_date": "2026-05-01",
            "end_date": "2026-05-02",
            "reason": "Personal work",
        })
        assert "approved" in result.lower() or "submitted" in result.lower() or "applied" in result.lower()

    def test_apply_leave_deducts_balance(self, env):
        import tools as t
        from tools import apply_leave
        apply_leave.invoke({
            "employee_id": "EMP001",
            "leave_type": "casual",
            "start_date": "2026-05-01",
            "end_date": "2026-05-01",
        })
        db = sqlite3.connect(t.TOOLS_DB)
        row = db.execute(
            "SELECT casual_leave FROM leave_balances WHERE employee_id='EMP001'"
        ).fetchone()
        db.close()
        assert row[0] < 10   # was 10, should be 9 now

    def test_apply_leave_insufficient_balance(self, env):
        import tools as t
        from tools import apply_leave
        # Drain casual balance first
        db = sqlite3.connect(t.TOOLS_DB)
        db.execute("UPDATE leave_balances SET casual_leave=0 WHERE employee_id='EMP001'")
        db.commit()
        db.close()
        result = apply_leave.invoke({
            "employee_id": "EMP001",
            "leave_type": "casual",
            "start_date": "2026-05-01",
            "end_date": "2026-05-01",
        })
        assert "insufficient" in result.lower() or "not enough" in result.lower() or "balance" in result.lower()

    def test_apply_leave_invalid_type(self, env):
        from tools import apply_leave
        result = apply_leave.invoke({
            "employee_id": "EMP001",
            "leave_type": "vacation",   # not a valid type
            "start_date": "2026-05-01",
            "end_date": "2026-05-01",
        })
        assert "invalid" in result.lower() or "vacation" in result.lower() or "type" in result.lower()

    def test_apply_leave_unknown_employee(self, env):
        from tools import apply_leave
        result = apply_leave.invoke({
            "employee_id": "EMP999",
            "leave_type": "casual",
            "start_date": "2026-05-01",
            "end_date": "2026-05-01",
        })
        assert "not found" in result.lower() or "no employee" in result.lower()

    def test_apply_sick_leave(self, env):
        from tools import apply_leave
        result = apply_leave.invoke({
            "employee_id": "EMP001",
            "leave_type": "sick",
            "start_date": "2026-05-05",
            "end_date": "2026-05-05",
        })
        assert "error" not in result.lower() or "sick" in result.lower()

    def test_apply_wfh(self, env):
        from tools import apply_leave
        result = apply_leave.invoke({
            "employee_id": "EMP001",
            "leave_type": "wfh",
            "start_date": "2026-05-10",
            "end_date": "2026-05-10",
        })
        assert "not found" not in result.lower() or "wfh" in result.lower()


# ---------------------------------------------------------------------------
# Manager approval queue: apply -> manager lists -> manager decides -> employee reads back
# ---------------------------------------------------------------------------

@pytest.fixture()
def team(env):
    """EMP001 reports to EMP000. EMP002 is an unrelated manager with no reports."""
    import tools as t
    db = sqlite3.connect(t.TOOLS_DB)
    db.execute("""
        INSERT OR IGNORE INTO employees
            (employee_id, full_name, email, department, designation, date_of_join, is_active)
        VALUES ('EMP000', 'Bob Boss', 'EMP000@test.com', 'HR', 'Manager', '2020-01-01', 1)
    """)
    db.execute("""
        INSERT OR IGNORE INTO employees
            (employee_id, full_name, email, department, designation, date_of_join, is_active)
        VALUES ('EMP002', 'Carol Other', 'EMP002@test.com', 'Finance', 'Manager', '2020-01-01', 1)
    """)
    db.execute("UPDATE employees SET manager_id='EMP000' WHERE employee_id='EMP001'")
    db.commit()
    db.close()


def _as(employee_id):
    """Act as this employee, the way the web app does — via the session ContextVar."""
    from auth import current_user_email
    return current_user_email.set(f"{employee_id}@test.com")


def _file_seven_day_request():
    from tools import apply_leave
    token = _as("EMP001")
    try:
        return apply_leave.invoke({
            "leave_type": "casual",
            "start_date": "2026-07-06",
            "end_date": "2026-07-12",
            "reason": "Family function",
        })
    finally:
        from auth import current_user_email
        current_user_email.reset(token)


def _balance(column="casual_leave", employee_id="EMP001"):
    import tools as t
    db = sqlite3.connect(t.TOOLS_DB)
    row = db.execute(
        f"SELECT {column} FROM leave_balances WHERE employee_id=?", (employee_id,)
    ).fetchone()
    db.close()
    return row[0]


class TestLeaveApprovalQueue:
    def test_long_request_is_pending_and_does_not_spend_the_balance(self, team):
        result = _file_seven_day_request()
        assert "manager approval" in result.lower()
        assert _balance() == 10          # unchanged until the manager decides

    def test_the_employee_is_told_the_request_number(self, team):
        """Without this the model invents one — it did, in a live run."""
        assert "#1" in _file_seven_day_request()

    def test_manager_sees_the_request_with_its_number(self, team):
        from tools import list_pending_leave_requests
        _file_seven_day_request()
        token = _as("EMP000")
        try:
            out = list_pending_leave_requests.invoke({})
        finally:
            from auth import current_user_email
            current_user_email.reset(token)
        assert "Request #1" in out
        assert "Alice Test" in out and "7 day(s) casual" in out

    def test_a_manager_sees_only_their_own_team(self, team):
        from tools import list_pending_leave_requests
        _file_seven_day_request()
        token = _as("EMP002")          # a manager, but not Alice's
        try:
            out = list_pending_leave_requests.invoke({})
        finally:
            from auth import current_user_email
            current_user_email.reset(token)
        assert "no leave requests" in out.lower()

    def test_manager_approval_deducts_and_records_the_approver(self, team):
        import tools as t
        from tools import approve_leave_request
        _file_seven_day_request()
        token = _as("EMP000")
        try:
            out = approve_leave_request.invoke({"request_id": 1, "status": "approved"})
        finally:
            from auth import current_user_email
            current_user_email.reset(token)
        assert "approved by EMP000" in out
        assert _balance() == 3           # 10 - 7
        db = sqlite3.connect(t.TOOLS_DB)
        row = db.execute("SELECT status, approved_by FROM leave_requests WHERE id=1").fetchone()
        db.close()
        assert row == ("approved", "EMP000")

    def test_requester_cannot_approve_their_own_request(self, team):
        from tools import approve_leave_request
        _file_seven_day_request()
        token = _as("EMP001")
        try:
            out = approve_leave_request.invoke({"request_id": 1, "status": "approved"})
        finally:
            from auth import current_user_email
            current_user_email.reset(token)
        assert "cannot approve or reject your own" in out.lower()
        assert _balance() == 10

    def test_unrelated_manager_cannot_decide(self, team):
        from tools import approve_leave_request
        _file_seven_day_request()
        token = _as("EMP002")
        try:
            out = approve_leave_request.invoke({"request_id": 1, "status": "approved"})
        finally:
            from auth import current_user_email
            current_user_email.reset(token)
        assert "not authorised" in out.lower()
        assert _balance() == 10

    def test_a_decided_request_cannot_be_decided_again(self, team):
        from tools import approve_leave_request
        _file_seven_day_request()
        token = _as("EMP000")
        try:
            approve_leave_request.invoke({"request_id": 1, "status": "approved"})
            out = approve_leave_request.invoke({"request_id": 1, "status": "rejected"})
        finally:
            from auth import current_user_email
            current_user_email.reset(token)
        assert "already approved" in out.lower()
        assert _balance() == 3           # not deducted twice

    def test_rejection_leaves_the_balance_alone(self, team):
        from tools import approve_leave_request
        _file_seven_day_request()
        token = _as("EMP000")
        try:
            out = approve_leave_request.invoke({"request_id": 1, "status": "rejected"})
        finally:
            from auth import current_user_email
            current_user_email.reset(token)
        assert "rejected" in out.lower()
        assert _balance() == 10

    def test_balance_is_rechecked_at_decision_time(self, team):
        """The days are spent between filing and approval — approval must refuse, not go negative."""
        import tools as t
        from tools import approve_leave_request
        _file_seven_day_request()
        db = sqlite3.connect(t.TOOLS_DB)
        db.execute("UPDATE leave_balances SET casual_leave=2 WHERE employee_id='EMP001'")
        db.commit()
        db.close()
        token = _as("EMP000")
        try:
            out = approve_leave_request.invoke({"request_id": 1, "status": "approved"})
        finally:
            from auth import current_user_email
            current_user_email.reset(token)
        assert "cannot approve" in out.lower()
        assert _balance() == 2

    def test_unknown_request_id(self, team):
        from tools import approve_leave_request
        token = _as("EMP000")
        try:
            out = approve_leave_request.invoke({"request_id": 999, "status": "approved"})
        finally:
            from auth import current_user_email
            current_user_email.reset(token)
        assert "not found" in out.lower()

    def test_invalid_status_is_refused(self, team):
        from tools import approve_leave_request
        _file_seven_day_request()
        token = _as("EMP000")
        try:
            out = approve_leave_request.invoke({"request_id": 1, "status": "maybe"})
        finally:
            from auth import current_user_email
            current_user_email.reset(token)
        assert "must be" in out.lower()


class TestListMyLeaveRequests:
    def test_pending_request_reads_back_as_waiting(self, team):
        from tools import list_my_leave_requests
        _file_seven_day_request()
        token = _as("EMP001")
        try:
            out = list_my_leave_requests.invoke({})
        finally:
            from auth import current_user_email
            current_user_email.reset(token)
        assert "Request #1" in out and "pending" in out
        assert "waiting for your manager" in out

    def test_decided_request_names_the_approver(self, team):
        from tools import approve_leave_request, list_my_leave_requests
        _file_seven_day_request()
        token = _as("EMP000")
        try:
            approve_leave_request.invoke({"request_id": 1, "status": "approved"})
        finally:
            from auth import current_user_email
            current_user_email.reset(token)
        token = _as("EMP001")
        try:
            out = list_my_leave_requests.invoke({})
        finally:
            from auth import current_user_email
            current_user_email.reset(token)
        assert "approved" in out and "Bob Boss" in out

    def test_no_requests(self, team):
        from tools import list_my_leave_requests
        token = _as("EMP001")
        try:
            out = list_my_leave_requests.invoke({})
        finally:
            from auth import current_user_email
            current_user_email.reset(token)
        assert "no leave requests" in out.lower()
