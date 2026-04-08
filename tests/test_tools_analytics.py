"""Tests for Analytics tools and Account tools."""

import os
import sys
import sqlite3
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


@pytest.fixture(autouse=True)
def setup_db(env):
    from tools import _get_db
    _get_db().close()
    import tools as t
    db = sqlite3.connect(t.TOOLS_DB)
    db.execute("""
        INSERT OR IGNORE INTO employees
            (employee_id, full_name, email, department, designation, date_of_join, is_active)
        VALUES ('EMP001', 'Eve Analytics', 'eve@test.com', 'Analytics', 'Analyst', '2022-01-01', 1)
    """)
    db.commit()
    db.close()
    # Seed some history messages
    hist_db = sqlite3.connect(t.HISTORY_DB)
    hist_db.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
            category TEXT DEFAULT '', confidence INTEGER DEFAULT 0,
            escalated INTEGER DEFAULT 0, fallback_used INTEGER DEFAULT 0,
            audit TEXT DEFAULT '', created_at TEXT NOT NULL
        )
    """)
    hist_db.execute("""
        INSERT INTO messages (email, role, content, category, confidence, escalated, fallback_used, created_at)
        VALUES ('eve@test.com', 'user', 'Test question', 'hr', 8, 0, 0, datetime('now'))
    """)
    hist_db.execute("""
        INSERT INTO messages (email, role, content, category, confidence, escalated, fallback_used, created_at)
        VALUES ('admin@test.com', 'assistant', 'Test answer', 'hr', 8, 0, 0, datetime('now'))
    """)
    hist_db.commit()
    hist_db.close()


class TestConversationStats:
    def test_returns_string(self, env):
        from tools import get_conversation_stats
        result = get_conversation_stats.invoke({"period": "all"})
        assert isinstance(result, str) and len(result) > 0

    def test_today_period(self, env):
        from tools import get_conversation_stats
        result = get_conversation_stats.invoke({"period": "today"})
        assert isinstance(result, str)

    def test_week_period(self, env):
        from tools import get_conversation_stats
        result = get_conversation_stats.invoke({"period": "week"})
        assert isinstance(result, str)

    def test_includes_message_count(self, env):
        from tools import get_conversation_stats
        result = get_conversation_stats.invoke({"period": "all"})
        # Should mention some numeric stat
        assert any(c.isdigit() for c in result)


class TestTicketSummary:
    def test_returns_string(self, env):
        from tools import get_ticket_summary
        result = get_ticket_summary.invoke({})
        assert isinstance(result, str)

    def test_reflects_created_tickets(self, env):
        from tools import create_ticket, get_ticket_summary
        create_ticket.invoke({"summary": "Analytics test ticket", "priority": "P3", "created_by": "EMP001"})
        result = get_ticket_summary.invoke({})
        assert "open" in result.lower() or "1" in result


class TestExpenseSummary:
    def test_returns_string(self, env):
        from tools import get_expense_summary
        result = get_expense_summary.invoke({})
        assert isinstance(result, str)

    def test_reflects_submitted_claims(self, env):
        from tools import submit_expense_claim, get_expense_summary
        submit_expense_claim.invoke({
            "employee_id": "EMP001",
            "amount": 500.0,
            "category": "travel",
            "description": "Analytics expense",
        })
        result = get_expense_summary.invoke({})
        assert "500" in result or "pending" in result.lower() or "1" in result


class TestLeaveSummary:
    def test_returns_string(self, env):
        from tools import get_leave_summary
        result = get_leave_summary.invoke({})
        assert isinstance(result, str)


class TestRoomUtilization:
    def test_returns_string(self, env):
        from tools import get_room_utilization
        result = get_room_utilization.invoke({})
        assert isinstance(result, str)


class TestChangeMyPassword:
    def test_change_password_with_correct_current(self, env):
        from auth import set_user_password
        from tools import change_my_password
        from auth import current_user_email
        # Set initial password
        set_user_password("eve@test.com", "oldpass")
        token = current_user_email.set("eve@test.com")
        try:
            result = change_my_password.invoke({
                "current_password": "oldpass",
                "new_password": "newpass123",
            })
            assert "success" in result.lower() or "changed" in result.lower() or "updated" in result.lower()
        finally:
            current_user_email.reset(token)

    def test_change_password_with_wrong_current(self, env):
        from auth import set_user_password
        from tools import change_my_password
        from auth import current_user_email
        set_user_password("eve@test.com", "realpass")
        token = current_user_email.set("eve@test.com")
        try:
            result = change_my_password.invoke({
                "current_password": "wrongpass",
                "new_password": "newpass123",
            })
            assert "incorrect" in result.lower() or "wrong" in result.lower() or "invalid" in result.lower()
        finally:
            current_user_email.reset(token)

    def test_new_password_actually_works(self, env):
        from auth import set_user_password, get_user_password, verify_password
        from tools import change_my_password
        from auth import current_user_email
        set_user_password("eve@test.com", "oldpass")
        token = current_user_email.set("eve@test.com")
        try:
            change_my_password.invoke({"current_password": "oldpass", "new_password": "mynewpass"})
        finally:
            current_user_email.reset(token)
        row = get_user_password("eve@test.com")
        assert verify_password("mynewpass", row["password_hash"], row["password_salt"])
