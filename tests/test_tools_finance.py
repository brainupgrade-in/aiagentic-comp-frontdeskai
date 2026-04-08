"""Tests for Finance domain tools: submit_expense_claim, get_expense_status, get_payslip."""

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
        VALUES ('EMP001', 'Carol Finance', 'carol@test.com', 'Finance', 'Analyst', '2022-01-01', 1)
    """)
    db.execute("""
        INSERT OR IGNORE INTO payslips
            (employee_id, month, gross_salary, deductions, net_salary, status, generated_at)
        VALUES ('EMP001', '2026-03', 90000, 18000, 72000, 'generated', '2026-03-31')
    """)
    db.commit()
    db.close()


class TestSubmitExpenseClaim:
    def test_submit_returns_claim_id(self, env):
        from tools import submit_expense_claim
        result = submit_expense_claim.invoke({
            "employee_id": "EMP001",
            "amount": 2500.0,
            "category": "travel",
            "description": "Client visit taxi",
            "receipt_count": 2,
        })
        assert "EXP-" in result

    def test_claim_stored_in_db(self, env):
        import tools as t
        from tools import submit_expense_claim
        submit_expense_claim.invoke({
            "employee_id": "EMP001",
            "amount": 500.0,
            "category": "meals",
            "description": "Team lunch",
        })
        db = sqlite3.connect(t.TOOLS_DB)
        row = db.execute(
            "SELECT * FROM expense_claims WHERE description='Team lunch'"
        ).fetchone()
        db.close()
        assert row is not None

    def test_claim_status_is_pending(self, env):
        import tools as t
        from tools import submit_expense_claim
        submit_expense_claim.invoke({
            "employee_id": "EMP001",
            "amount": 100.0,
            "category": "office_supplies",
            "description": "Printer cartridge",
        })
        db = sqlite3.connect(t.TOOLS_DB)
        row = db.execute(
            "SELECT status FROM expense_claims WHERE description='Printer cartridge'"
        ).fetchone()
        db.close()
        assert row[0].lower() in ("pending", "submitted")

    @pytest.mark.parametrize("category", [
        "travel", "meals", "software", "hardware", "training", "office_supplies", "other"
    ])
    def test_all_valid_categories(self, env, category):
        from tools import submit_expense_claim
        result = submit_expense_claim.invoke({
            "employee_id": "EMP001",
            "amount": 100.0,
            "category": category,
            "description": f"Test {category}",
        })
        assert "EXP-" in result


class TestGetExpenseStatus:
    def test_get_existing_claim(self, env):
        from tools import submit_expense_claim, get_expense_status
        submit_result = submit_expense_claim.invoke({
            "employee_id": "EMP001",
            "amount": 300.0,
            "category": "travel",
            "description": "Flight to conference",
        })
        claim_id = [w for w in submit_result.split() if w.startswith("EXP-")][0].rstrip(".")
        status = get_expense_status.invoke({"claim_id": claim_id})
        assert "Flight to conference" in status or claim_id in status

    def test_get_nonexistent_claim(self, env):
        from tools import get_expense_status
        result = get_expense_status.invoke({"claim_id": "EXP-2020-9999"})
        assert "not found" in result.lower() or "9999" in result

    def test_status_includes_amount(self, env):
        from tools import submit_expense_claim, get_expense_status
        submit_result = submit_expense_claim.invoke({
            "employee_id": "EMP001",
            "amount": 1234.56,
            "category": "training",
            "description": "Course fees",
        })
        claim_id = [w for w in submit_result.split() if w.startswith("EXP-")][0].rstrip(".")
        status = get_expense_status.invoke({"claim_id": claim_id})
        assert "1,234" in status or "1234" in status


class TestGetPayslip:
    def test_get_existing_payslip(self, env):
        from tools import get_payslip
        result = get_payslip.invoke({"employee_id": "EMP001", "month": "2026-03"})
        assert "72000" in result or "net" in result.lower()

    def test_get_nonexistent_payslip(self, env):
        from tools import get_payslip
        result = get_payslip.invoke({"employee_id": "EMP001", "month": "2020-01"})
        assert "not found" in result.lower() or "no payslip" in result.lower() or "no salary" in result.lower()

    def test_get_payslip_unknown_employee(self, env):
        from tools import get_payslip
        result = get_payslip.invoke({"employee_id": "EMP999", "month": "2026-03"})
        assert "not found" in result.lower() or "no salary" in result.lower()

    def test_payslip_includes_gross_and_deductions(self, env):
        from tools import get_payslip
        result = get_payslip.invoke({"employee_id": "EMP001", "month": "2026-03"})
        assert "90000" in result or "gross" in result.lower()
        assert "18000" in result or "deduction" in result.lower()
