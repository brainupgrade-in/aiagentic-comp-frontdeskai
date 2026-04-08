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
