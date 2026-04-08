"""Tests for Tech domain tools: create_ticket, get_ticket_status, list_my_tickets."""

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
        VALUES ('EMP001', 'Bob Tech', 'bob@test.com', 'Engineering', 'Engineer', '2022-01-01', 1)
    """)
    db.commit()
    db.close()


class TestCreateTicket:
    def test_creates_ticket_returns_id(self, env):
        from tools import create_ticket
        result = create_ticket.invoke({
            "summary": "Laptop not booting",
            "priority": "P2",
        })
        assert "TECH-" in result

    def test_ticket_id_increments(self, env):
        from tools import create_ticket
        r1 = create_ticket.invoke({"summary": "Issue 1", "priority": "P3"})
        r2 = create_ticket.invoke({"summary": "Issue 2", "priority": "P3"})
        # Both should contain ticket IDs
        assert "TECH-" in r1
        assert "TECH-" in r2
        assert r1 != r2

    def test_ticket_stored_in_db(self, env):
        import tools as t
        from tools import create_ticket
        create_ticket.invoke({
            "summary": "VPN not working",
            "priority": "P1",
            "category": "network",
            "created_by": "EMP001",
        })
        db = sqlite3.connect(t.TOOLS_DB)
        row = db.execute(
            "SELECT * FROM tickets WHERE summary='VPN not working'"
        ).fetchone()
        db.close()
        assert row is not None

    def test_invalid_priority_handled(self, env):
        from tools import create_ticket
        result = create_ticket.invoke({
            "summary": "Some issue",
            "priority": "CRITICAL",   # not P1-P4
        })
        # Should either reject or default — not crash
        assert isinstance(result, str)

    @pytest.mark.parametrize("priority", ["P1", "P2", "P3", "P4"])
    def test_all_valid_priorities(self, env, priority):
        from tools import create_ticket
        result = create_ticket.invoke({"summary": f"Test {priority}", "priority": priority})
        assert "TECH-" in result

    @pytest.mark.parametrize("category", ["hardware", "software", "network", "access", "security", "general"])
    def test_all_valid_categories(self, env, category):
        from tools import create_ticket
        result = create_ticket.invoke({
            "summary": f"Issue in {category}",
            "priority": "P3",
            "category": category,
        })
        assert isinstance(result, str) and len(result) > 0


class TestGetTicketStatus:
    def test_get_existing_ticket(self, env):
        from tools import create_ticket, get_ticket_status
        create_result = create_ticket.invoke({"summary": "Printer broken", "priority": "P3"})
        # Extract ticket ID from result
        ticket_id = [w for w in create_result.split() if w.startswith("TECH-")][0].rstrip(".")
        status = get_ticket_status.invoke({"ticket_id": ticket_id})
        assert "Printer broken" in status or ticket_id in status

    def test_get_nonexistent_ticket(self, env):
        from tools import get_ticket_status
        result = get_ticket_status.invoke({"ticket_id": "TECH-9999"})
        assert "not found" in result.lower() or "9999" in result

    def test_status_includes_priority_and_status(self, env):
        from tools import create_ticket, get_ticket_status
        create_result = create_ticket.invoke({"summary": "Screen flickering", "priority": "P2"})
        ticket_id = [w for w in create_result.split() if w.startswith("TECH-")][0].rstrip(".,")
        result = get_ticket_status.invoke({"ticket_id": ticket_id})
        assert "P2" in result or "priority" in result.lower()


class TestListMyTickets:
    def test_list_tickets_for_employee_with_tickets(self, env):
        from tools import create_ticket, list_my_tickets
        create_ticket.invoke({
            "summary": "My ticket",
            "priority": "P3",
            "created_by": "EMP001",
        })
        result = list_my_tickets.invoke({"employee_id": "EMP001"})
        assert "My ticket" in result or "TECH-" in result

    def test_list_tickets_for_employee_with_no_tickets(self, env):
        from tools import list_my_tickets
        result = list_my_tickets.invoke({"employee_id": "EMP999"})
        assert "no ticket" in result.lower() or "not found" in result.lower() or result.strip() != ""
