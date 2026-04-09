"""Tool definitions for FrontDesk AI agents.

Each domain (HR, Tech, Finance, Facilities) has tools that agents can call
to look up data or take actions. Backed by a SQLite database with proper
schema, constraints, indexes, and foreign keys.
"""

import os
import sqlite3
import smtplib
import hashlib
import base64
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from langchain_core.tools import tool

TOOLS_DB = os.path.join(os.getenv("SQLITE_DIR", "/shared/.sqlite"), "frontdesk_tools.db")
HISTORY_DB = os.path.join(os.getenv("SQLITE_DIR", "/shared/.sqlite"), "history.db")

_db_initialized = False


def _get_db() -> sqlite3.Connection:
    """Get a connection with FK enforcement and row factory. Initialize schema on first call."""
    global _db_initialized
    os.makedirs(os.path.dirname(TOOLS_DB), exist_ok=True)
    conn = sqlite3.connect(TOOLS_DB)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    if not _db_initialized:
        _init_schema(conn)
        _db_initialized = True
    return conn


def _init_schema(conn: sqlite3.Connection):
    """Create all tables, indexes, and seed reference data (idempotent)."""
    conn.executescript("""
        -- =============================================
        -- EMPLOYEES: master table for all employee data
        -- =============================================
        CREATE TABLE IF NOT EXISTS employees (
            employee_id   TEXT PRIMARY KEY,              -- username part of email
            full_name     TEXT NOT NULL,
            email         TEXT NOT NULL UNIQUE,
            department    TEXT NOT NULL DEFAULT 'general',
            designation   TEXT NOT NULL DEFAULT 'Employee',
            date_of_join  TEXT NOT NULL DEFAULT (date('now')),
            is_active     INTEGER NOT NULL DEFAULT 1,
            created_at    TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_employees_dept ON employees(department);
        CREATE INDEX IF NOT EXISTS idx_employees_active ON employees(is_active);

        -- =============================================
        -- LEAVE BALANCES: annual allocation per employee
        -- =============================================
        CREATE TABLE IF NOT EXISTS leave_balances (
            employee_id   TEXT PRIMARY KEY
                          REFERENCES employees(employee_id) ON DELETE CASCADE,
            casual_leave  INTEGER NOT NULL DEFAULT 12 CHECK(casual_leave >= 0),
            sick_leave    INTEGER NOT NULL DEFAULT 6  CHECK(sick_leave >= 0),
            earned_leave  INTEGER NOT NULL DEFAULT 15 CHECK(earned_leave >= 0),
            wfh_days      INTEGER NOT NULL DEFAULT 24 CHECK(wfh_days >= 0),
            year          INTEGER NOT NULL DEFAULT (CAST(strftime('%Y','now') AS INTEGER)),
            updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
        );

        -- =============================================
        -- LEAVE REQUESTS: application + approval workflow
        -- =============================================
        CREATE TABLE IF NOT EXISTS leave_requests (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id   TEXT NOT NULL
                          REFERENCES employees(employee_id) ON DELETE CASCADE,
            leave_type    TEXT NOT NULL CHECK(leave_type IN ('casual','sick','earned','wfh')),
            start_date    TEXT NOT NULL,                 -- YYYY-MM-DD
            end_date      TEXT NOT NULL,                 -- YYYY-MM-DD
            days          INTEGER NOT NULL CHECK(days > 0),
            reason        TEXT NOT NULL DEFAULT '',
            status        TEXT NOT NULL DEFAULT 'pending'
                          CHECK(status IN ('pending','approved','rejected','cancelled')),
            approved_by   TEXT REFERENCES employees(employee_id),
            created_at    TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_leave_req_emp ON leave_requests(employee_id);
        CREATE INDEX IF NOT EXISTS idx_leave_req_status ON leave_requests(status);
        CREATE INDEX IF NOT EXISTS idx_leave_req_dates ON leave_requests(start_date, end_date);

        -- =============================================
        -- TICKETS: IT support ticket management
        -- =============================================
        CREATE TABLE IF NOT EXISTS tickets (
            ticket_id     TEXT PRIMARY KEY,               -- e.g. TECH-1001
            summary       TEXT NOT NULL,
            description   TEXT NOT NULL DEFAULT '',
            priority      TEXT NOT NULL DEFAULT 'P3'
                          CHECK(priority IN ('P1','P2','P3','P4')),
            status        TEXT NOT NULL DEFAULT 'Open'
                          CHECK(status IN ('Open','In Progress','Resolved','Closed','On Hold')),
            category      TEXT NOT NULL DEFAULT 'general'
                          CHECK(category IN ('hardware','software','network','access','security','general')),
            assignee      TEXT REFERENCES employees(employee_id),
            created_by    TEXT REFERENCES employees(employee_id),
            resolved_at   TEXT,
            sla_hours     INTEGER NOT NULL DEFAULT 48,    -- derived from priority
            created_at    TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
        CREATE INDEX IF NOT EXISTS idx_tickets_priority ON tickets(priority);
        CREATE INDEX IF NOT EXISTS idx_tickets_assignee ON tickets(assignee);
        CREATE INDEX IF NOT EXISTS idx_tickets_created_by ON tickets(created_by);

        -- =============================================
        -- TICKET COMMENTS: activity log on tickets
        -- =============================================
        CREATE TABLE IF NOT EXISTS ticket_comments (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id     TEXT NOT NULL
                          REFERENCES tickets(ticket_id) ON DELETE CASCADE,
            author        TEXT NOT NULL
                          REFERENCES employees(employee_id),
            comment       TEXT NOT NULL,
            created_at    TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_tcomments_ticket ON ticket_comments(ticket_id);

        -- =============================================
        -- EXPENSE CLAIMS: reimbursement tracking
        -- =============================================
        CREATE TABLE IF NOT EXISTS expense_claims (
            claim_id      TEXT PRIMARY KEY,               -- e.g. EXP-2026-0001
            employee_id   TEXT NOT NULL
                          REFERENCES employees(employee_id) ON DELETE CASCADE,
            amount        REAL NOT NULL CHECK(amount > 0),
            currency      TEXT NOT NULL DEFAULT 'INR',
            category      TEXT NOT NULL
                          CHECK(category IN ('travel','meals','software','hardware','training','office_supplies','other')),
            description   TEXT NOT NULL,
            receipt_count INTEGER NOT NULL DEFAULT 0,
            status        TEXT NOT NULL DEFAULT 'submitted'
                          CHECK(status IN ('draft','submitted','under_review','approved','rejected','paid')),
            reviewed_by   TEXT REFERENCES employees(employee_id),
            rejection_reason TEXT,
            submitted_at  TEXT NOT NULL DEFAULT (datetime('now')),
            reviewed_at   TEXT,
            paid_at       TEXT,
            created_at    TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_expense_emp ON expense_claims(employee_id);
        CREATE INDEX IF NOT EXISTS idx_expense_status ON expense_claims(status);

        -- =============================================
        -- MEETING ROOMS: room master data
        -- =============================================
        CREATE TABLE IF NOT EXISTS meeting_rooms (
            room_id       TEXT PRIMARY KEY,               -- slug: ganges, yamuna, ...
            room_name     TEXT NOT NULL UNIQUE,            -- display name
            capacity      INTEGER NOT NULL CHECK(capacity > 0),
            floor         TEXT NOT NULL,
            has_projector INTEGER NOT NULL DEFAULT 1,
            has_whiteboard INTEGER NOT NULL DEFAULT 1,
            has_video_conf INTEGER NOT NULL DEFAULT 0,
            is_active     INTEGER NOT NULL DEFAULT 1
        );

        -- =============================================
        -- ROOM BOOKINGS: reservation system
        -- =============================================
        CREATE TABLE IF NOT EXISTS room_bookings (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id       TEXT NOT NULL
                          REFERENCES meeting_rooms(room_id) ON DELETE CASCADE,
            date          TEXT NOT NULL,                  -- YYYY-MM-DD
            start_time    TEXT NOT NULL,                  -- HH:MM (24h)
            end_time      TEXT NOT NULL,                  -- HH:MM (24h)
            booked_by     TEXT NOT NULL
                          REFERENCES employees(employee_id),
            purpose       TEXT NOT NULL DEFAULT 'Meeting',
            attendees     INTEGER NOT NULL DEFAULT 2,
            status        TEXT NOT NULL DEFAULT 'confirmed'
                          CHECK(status IN ('confirmed','cancelled')),
            created_at    TEXT NOT NULL DEFAULT (datetime('now')),
            CHECK(end_time > start_time)
        );
        CREATE INDEX IF NOT EXISTS idx_bookings_room_date ON room_bookings(room_id, date);
        CREATE INDEX IF NOT EXISTS idx_bookings_booked_by ON room_bookings(booked_by);
        CREATE INDEX IF NOT EXISTS idx_bookings_status ON room_bookings(status);

        -- =============================================
        -- PAYROLL: salary slip tracking
        -- =============================================
        CREATE TABLE IF NOT EXISTS payslips (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id   TEXT NOT NULL
                          REFERENCES employees(employee_id) ON DELETE CASCADE,
            month         TEXT NOT NULL,                  -- YYYY-MM
            gross_salary  REAL NOT NULL CHECK(gross_salary > 0),
            deductions    REAL NOT NULL DEFAULT 0 CHECK(deductions >= 0),
            net_salary    REAL NOT NULL CHECK(net_salary > 0),
            status        TEXT NOT NULL DEFAULT 'generated'
                          CHECK(status IN ('generated','dispatched','acknowledged')),
            generated_at  TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(employee_id, month)
        );
        CREATE INDEX IF NOT EXISTS idx_payslips_emp ON payslips(employee_id);
        CREATE INDEX IF NOT EXISTS idx_payslips_month ON payslips(month);
    """)

    # =============================================
    # SYSTEM CONFIG: runtime settings (LLM model, etc.)
    # =============================================
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS system_config (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_by TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );
    """)
    # Seed defaults (idempotent)
    config_defaults = [
        ("llm_provider",    "ollama"),
        ("llm_model",       "llama3.3:70b"),
        ("llm_temperature", "0"),
        ("llm_api_key",     ""),
        ("llm_fallback_provider",    "groq"),
        ("llm_fallback_model",       "llama-3.3-70b-versatile"),
        ("llm_fallback_temperature", "0"),
        ("llm_fallback_api_key",     ""),
        ("smtp_host",       ""),
        ("smtp_port",       "587"),
        ("smtp_username",   ""),
        ("smtp_password_enc", ""),
        ("smtp_from_email", ""),
        ("smtp_use_tls",    "true"),
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO system_config (key, value) VALUES (?, ?)",
        config_defaults,
    )

    # ---------- Demo seed data (only when SEED_DEMO_DATA=true) ----------
    if os.getenv("SEED_DEMO_DATA", "").lower() != "true":
        conn.commit()
        return

    employees = [
        ("rajesh",       "Rajesh Gheware",  "rajesh@unigps.in",       "engineering", "CTO",               "2020-01-15"),
        ("admin",        "Admin User",      "admin@unigps.in",        "admin",       "System Admin",      "2020-01-01"),
        ("priya.sharma", "Priya Sharma",    "priya.sharma@unigps.in", "engineering", "Senior Developer",  "2022-06-10"),
        ("amit.patel",   "Amit Patel",      "amit.patel@unigps.in",   "engineering", "DevOps Engineer",   "2023-03-20"),
        ("neha.gupta",   "Neha Gupta",      "neha.gupta@unigps.in",   "product",     "Product Manager",   "2023-09-01"),
        ("suresh.kumar", "Suresh Kumar",    "suresh.kumar@unigps.in", "finance",     "Finance Manager",   "2021-04-12"),
        ("anita.verma",  "Anita Verma",     "anita.verma@unigps.in",  "hr",          "HR Manager",        "2021-02-01"),
        ("it.support",   "IT Support Desk", "it.support@unigps.in",   "it",          "IT Support Lead",   "2020-01-01"),
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO employees (employee_id, full_name, email, department, designation, date_of_join) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        employees,
    )

    leave_balances = [
        ("rajesh",        8,  5,  12, 20),
        ("admin",        12,  6,  15, 24),
        ("priya.sharma",  5,  3,  10, 18),
        ("amit.patel",   10,  6,  14, 22),
        ("neha.gupta",    3,  2,   8, 15),
        ("suresh.kumar", 11,  6,  13, 22),
        ("anita.verma",   9,  4,  11, 20),
        ("it.support",   12,  6,  15, 24),
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO leave_balances (employee_id, casual_leave, sick_leave, earned_leave, wfh_days) "
        "VALUES (?, ?, ?, ?, ?)",
        leave_balances,
    )

    # Pre-existing leave requests
    leave_requests = [
        ("priya.sharma", "casual",  "2026-02-10", "2026-02-11", 2, "Family function",   "approved", "anita.verma"),
        ("amit.patel",   "sick",    "2026-02-05", "2026-02-05", 1, "Not feeling well",  "approved", "anita.verma"),
        ("neha.gupta",   "earned",  "2026-03-01", "2026-03-07", 7, "Vacation to Goa",   "pending",  None),
    ]
    for lr in leave_requests:
        conn.execute(
            "INSERT OR IGNORE INTO leave_requests "
            "(employee_id, leave_type, start_date, end_date, days, reason, status, approved_by) "
            "SELECT ?, ?, ?, ?, ?, ?, ?, ? "
            "WHERE NOT EXISTS (SELECT 1 FROM leave_requests WHERE employee_id=? AND start_date=? AND end_date=?)",
            (*lr, lr[0], lr[2], lr[3]),
        )

    tickets = [
        ("TECH-1001", "VPN not connecting from home",      "Getting timeout errors when connecting via Cisco AnyConnect from home WiFi. Tried restarting the client.",
         "P2", "In Progress", "network",   "it.support",  "priya.sharma", None, 24, "2026-02-17 09:15:00"),
        ("TECH-1002", "Need AWS console access",           "Require read access to production S3 buckets and CloudWatch logs for debugging.",
         "P3", "Open",        "access",    None,           "amit.patel",   None, 48, "2026-02-18 11:30:00"),
        ("TECH-1003", "Laptop screen flickering",          "Dell XPS 15 screen flickers intermittently, especially when on battery power.",
         "P3", "Open",        "hardware",  None,           "neha.gupta",   None, 48, "2026-02-20 14:00:00"),
        ("TECH-1004", "Jira dashboard loading slow",       "Jira dashboards take 15+ seconds to load. Other team members reporting same issue.",
         "P2", "Resolved",    "software",  "it.support",  "rajesh",       "2026-02-19 16:30:00", 24, "2026-02-19 10:00:00"),
        ("TECH-1005", "New joiner laptop setup",           "Need a MacBook Pro M3 setup with standard dev tools for new hire starting Feb 24.",
         "P4", "Open",        "hardware",  None,           "anita.verma",  None, 72, "2026-02-21 08:45:00"),
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO tickets "
        "(ticket_id, summary, description, priority, status, category, assignee, created_by, resolved_at, sla_hours, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        tickets,
    )

    ticket_comments = [
        ("TECH-1001", "priya.sharma", "Tried restarting laptop and router, still not working."),
        ("TECH-1001", "it.support",   "Checked VPN server logs. Session is timing out at the firewall. Escalating to network team."),
        ("TECH-1004", "it.support",   "Identified the issue — Jira indexing job was stuck. Cleared the queue and restarted the service."),
        ("TECH-1004", "rajesh",       "Confirmed it's loading fast now. Thanks!"),
    ]
    for tc in ticket_comments:
        conn.execute(
            "INSERT OR IGNORE INTO ticket_comments (ticket_id, author, comment) "
            "SELECT ?, ?, ? "
            "WHERE NOT EXISTS (SELECT 1 FROM ticket_comments WHERE ticket_id=? AND author=? AND comment=?)",
            (*tc, *tc),
        )

    expense_claims = [
        ("EXP-2026-0001", "rajesh",       4500.00,  "travel",    "Client visit to Mumbai — flight tickets (BLR-BOM round trip)",
         2, "approved",    "suresh.kumar", None,           "2026-02-10", "2026-02-15", "2026-02-18"),
        ("EXP-2026-0002", "priya.sharma", 1200.00,  "software",  "JetBrains IntelliJ IDEA Ultimate — annual license renewal",
         1, "submitted",   None,           None,           "2026-02-18", None,          None),
        ("EXP-2026-0003", "amit.patel",    850.00,  "meals",     "Team dinner — Q4 project celebration at Barbeque Nation",
         1, "under_review","suresh.kumar", None,           "2026-02-19", None,          None),
        ("EXP-2026-0004", "neha.gupta",  15000.00,  "training",  "AWS Solutions Architect Professional course on Udemy",
         1, "rejected",    "suresh.kumar", "Exceeds per-course limit of Rs. 10,000. Please get VP approval.", "2026-02-05", "2026-02-12", None),
        ("EXP-2026-0005", "rajesh",       2200.00,  "travel",    "Cab to airport and back for Mumbai client visit",
         2, "paid",        "suresh.kumar", None,           "2026-02-10", "2026-02-15", "2026-02-20"),
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO expense_claims "
        "(claim_id, employee_id, amount, category, description, receipt_count, status, reviewed_by, rejection_reason, submitted_at, reviewed_at, paid_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        expense_claims,
    )

    rooms = [
        ("ganges",   "Ganges",   10, "2nd Floor", 1, 1, 1),
        ("yamuna",   "Yamuna",    6, "2nd Floor", 1, 0, 0),
        ("kaveri",   "Kaveri",   20, "3rd Floor", 1, 1, 1),
        ("narmada",  "Narmada",   4, "1st Floor", 0, 1, 0),
        ("godavari", "Godavari", 12, "3rd Floor", 1, 1, 1),
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO meeting_rooms "
        "(room_id, room_name, capacity, floor, has_projector, has_whiteboard, has_video_conf) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rooms,
    )

    bookings = [
        ("ganges",  "2026-02-20", "10:00", "11:00", "rajesh",       "Sprint planning",           6),
        ("kaveri",  "2026-02-20", "14:00", "15:30", "priya.sharma", "Design review",             8),
        ("ganges",  "2026-02-21", "09:00", "10:00", "amit.patel",   "Morning standup",           4),
        ("godavari","2026-02-20", "11:00", "12:00", "neha.gupta",   "Product roadmap discussion",5),
    ]
    for b in bookings:
        conn.execute(
            "INSERT OR IGNORE INTO room_bookings (room_id, date, start_time, end_time, booked_by, purpose, attendees) "
            "SELECT ?, ?, ?, ?, ?, ?, ? "
            "WHERE NOT EXISTS (SELECT 1 FROM room_bookings WHERE room_id=? AND date=? AND start_time=?)",
            (*b, b[0], b[1], b[2]),
        )

    # Payslips for last 3 months
    payslip_data = [
        ("rajesh",       "2025-12", 250000, 62500,  187500),
        ("rajesh",       "2026-01", 250000, 62500,  187500),
        ("priya.sharma", "2025-12", 150000, 37500,  112500),
        ("priya.sharma", "2026-01", 150000, 37500,  112500),
        ("amit.patel",   "2025-12", 120000, 30000,   90000),
        ("amit.patel",   "2026-01", 120000, 30000,   90000),
        ("neha.gupta",   "2025-12", 140000, 35000,  105000),
        ("neha.gupta",   "2026-01", 140000, 35000,  105000),
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO payslips (employee_id, month, gross_salary, deductions, net_salary) "
        "VALUES (?, ?, ?, ?, ?)",
        payslip_data,
    )

    conn.commit()


# ==========================================
# Sequence generator for ticket / claim IDs
# ==========================================

def _next_ticket_id(conn: sqlite3.Connection) -> str:
    """Generate next TECH-NNNN ticket ID."""
    row = conn.execute(
        "SELECT ticket_id FROM tickets ORDER BY ticket_id DESC LIMIT 1"
    ).fetchone()
    if row:
        num = int(row["ticket_id"].split("-")[1]) + 1
    else:
        num = 1001
    return f"TECH-{num}"


def _next_claim_id(conn: sqlite3.Connection) -> str:
    """Generate next EXP-YYYY-NNNN claim ID."""
    year = datetime.now().strftime("%Y")
    row = conn.execute(
        "SELECT claim_id FROM expense_claims WHERE claim_id LIKE ? ORDER BY claim_id DESC LIMIT 1",
        (f"EXP-{year}-%",),
    ).fetchone()
    if row:
        num = int(row["claim_id"].split("-")[2]) + 1
    else:
        num = 1
    return f"EXP-{year}-{num:04d}"


# ========== HR TOOLS ==========

@tool
def get_leave_balance(employee_id: str) -> str:
    """Look up an employee's current leave balance. The employee_id is the part before @ in their email (e.g. 'rajesh' for rajesh@unigps.in)."""
    conn = _get_db()
    try:
        emp = conn.execute(
            "SELECT e.full_name, e.department, lb.* FROM employees e "
            "JOIN leave_balances lb ON e.employee_id = lb.employee_id "
            "WHERE e.employee_id = ? AND e.is_active = 1",
            (employee_id,),
        ).fetchone()
        if not emp:
            return f"No leave balance found for employee '{employee_id}'. They may need to contact HR to set up their account."

        # Also get pending leave requests
        pending = conn.execute(
            "SELECT leave_type, start_date, end_date, days FROM leave_requests "
            "WHERE employee_id = ? AND status = 'pending' ORDER BY start_date",
            (employee_id,),
        ).fetchall()

        lines = [
            f"Leave balance for {emp['full_name']} ({employee_id}) — {emp['department'].title()} dept:",
            f"  Casual Leave:  {emp['casual_leave']} days remaining",
            f"  Sick Leave:    {emp['sick_leave']} days remaining",
            f"  Earned Leave:  {emp['earned_leave']} days remaining",
            f"  WFH Days:      {emp['wfh_days']} days remaining",
        ]
        if pending:
            lines.append(f"\nPending leave requests:")
            for p in pending:
                lines.append(f"  - {p['leave_type'].title()} leave: {p['start_date']} to {p['end_date']} ({p['days']} days)")
        return "\n".join(lines)
    finally:
        conn.close()


@tool
def apply_leave(employee_id: str, leave_type: str, start_date: str, end_date: str, reason: str = "") -> str:
    """Apply for leave on behalf of an employee. leave_type must be one of: casual, sick, earned, wfh. Dates in YYYY-MM-DD format."""
    valid_types = ("casual", "sick", "earned", "wfh")
    if leave_type not in valid_types:
        return f"Invalid leave type '{leave_type}'. Must be one of: {', '.join(valid_types)}"

    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        return "Invalid date format. Use YYYY-MM-DD."

    if end < start:
        return "End date cannot be before start date."

    days = (end - start).days + 1

    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM leave_balances WHERE employee_id = ?", (employee_id,)
        ).fetchone()
        if not row:
            return f"Employee '{employee_id}' not found in the system."

        # Explicit mapping — never construct column names from input
        _LEAVE_COLUMNS = {"casual": "casual_leave", "sick": "sick_leave", "earned": "earned_leave", "wfh": "wfh_days"}
        col = _LEAVE_COLUMNS[leave_type]  # safe: leave_type already validated above
        available = row[col]
        if days > available:
            return f"Insufficient {leave_type} leave. Requested {days} days but only {available} remaining."

        # Check for overlapping approved/pending requests
        overlap = conn.execute(
            "SELECT * FROM leave_requests WHERE employee_id = ? "
            "AND status IN ('pending', 'approved') "
            "AND NOT (end_date < ? OR start_date > ?)",
            (employee_id, start_date, end_date),
        ).fetchone()
        if overlap:
            return (
                f"Overlapping leave found: {overlap['leave_type']} leave from "
                f"{overlap['start_date']} to {overlap['end_date']} (status: {overlap['status']}). "
                f"Please cancel it first or choose different dates."
            )

        # Auto-approve <= 3 days, otherwise pending for manager approval
        auto_approve = days <= 3
        status = "approved" if auto_approve else "pending"

        conn.execute(
            "INSERT INTO leave_requests (employee_id, leave_type, start_date, end_date, days, reason, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (employee_id, leave_type, start_date, end_date, days, reason, status),
        )

        if auto_approve:
            conn.execute(
                f"UPDATE leave_balances SET {col} = {col} - ?, updated_at = datetime('now') "
                "WHERE employee_id = ?",
                (days, employee_id),
            )
            remaining = available - days
            result = (
                f"Leave approved! {days} day(s) of {leave_type} leave from {start_date} to {end_date}.\n"
                f"Remaining {leave_type}: {remaining} days."
            )
        else:
            result = (
                f"Leave request submitted for manager approval: {days} day(s) of {leave_type} leave "
                f"from {start_date} to {end_date}.\n"
                f"Requests of more than 3 days require manager approval. You'll be notified once reviewed."
            )

        conn.commit()
        return result
    finally:
        conn.close()


# ========== TECH TOOLS ==========

@tool
def create_ticket(summary: str, priority: str, category: str = "general", description: str = "", created_by: str = "") -> str:
    """Create a new IT support ticket. priority: P1/P2/P3/P4. category: hardware/software/network/access/security/general."""
    valid_priorities = {"P1": 4, "P2": 24, "P3": 48, "P4": 72}  # SLA hours
    valid_categories = ("hardware", "software", "network", "access", "security", "general")

    if priority not in valid_priorities:
        return f"Invalid priority '{priority}'. Must be one of: {', '.join(valid_priorities)}"
    if category not in valid_categories:
        return f"Invalid category '{category}'. Must be one of: {', '.join(valid_categories)}"

    conn = _get_db()
    try:
        # Verify creator exists
        if created_by:
            emp = conn.execute("SELECT 1 FROM employees WHERE employee_id = ?", (created_by,)).fetchone()
            if not emp:
                return f"Employee '{created_by}' not found."

        ticket_id = _next_ticket_id(conn)
        sla = valid_priorities[priority]

        conn.execute(
            "INSERT INTO tickets (ticket_id, summary, description, priority, category, created_by, sla_hours) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ticket_id, summary, description, priority, category, created_by or None, sla),
        )
        conn.commit()

        return (
            f"Ticket created successfully!\n"
            f"  Ticket ID: {ticket_id}\n"
            f"  Summary: {summary}\n"
            f"  Priority: {priority} (SLA: {sla} hours)\n"
            f"  Category: {category}\n"
            f"  Status: Open"
        )
    finally:
        conn.close()


@tool
def get_ticket_status(ticket_id: str) -> str:
    """Check the status of an IT support ticket by its ID (e.g. TECH-1001). Shows full details and recent comments."""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT t.*, e.full_name AS creator_name, a.full_name AS assignee_name "
            "FROM tickets t "
            "LEFT JOIN employees e ON t.created_by = e.employee_id "
            "LEFT JOIN employees a ON t.assignee = a.employee_id "
            "WHERE t.ticket_id = ?",
            (ticket_id.upper(),),
        ).fetchone()
        if not row:
            return f"Ticket '{ticket_id}' not found."

        lines = [
            f"Ticket {row['ticket_id']}:",
            f"  Summary:    {row['summary']}",
            f"  Priority:   {row['priority']} (SLA: {row['sla_hours']}h)",
            f"  Status:     {row['status']}",
            f"  Category:   {row['category']}",
            f"  Created by: {row['creator_name'] or 'System'}",
            f"  Assignee:   {row['assignee_name'] or 'Unassigned'}",
            f"  Created:    {row['created_at']}",
        ]
        if row["resolved_at"]:
            lines.append(f"  Resolved:   {row['resolved_at']}")

        # Fetch recent comments
        comments = conn.execute(
            "SELECT tc.comment, tc.created_at, e.full_name "
            "FROM ticket_comments tc JOIN employees e ON tc.author = e.employee_id "
            "WHERE tc.ticket_id = ? ORDER BY tc.created_at DESC LIMIT 5",
            (ticket_id.upper(),),
        ).fetchall()
        if comments:
            lines.append(f"\nRecent activity ({len(comments)} comments):")
            for c in reversed(comments):
                lines.append(f"  [{c['created_at']}] {c['full_name']}: {c['comment']}")

        return "\n".join(lines)
    finally:
        conn.close()


@tool
def list_my_tickets(employee_id: str) -> str:
    """List all open/in-progress tickets created by or assigned to an employee."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT ticket_id, summary, priority, status, category "
            "FROM tickets "
            "WHERE (created_by = ? OR assignee = ?) AND status NOT IN ('Closed') "
            "ORDER BY CASE priority WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 WHEN 'P3' THEN 3 ELSE 4 END, created_at DESC",
            (employee_id, employee_id),
        ).fetchall()
        if not rows:
            return f"No open tickets found for '{employee_id}'."

        lines = [f"Open tickets for {employee_id} ({len(rows)} total):"]
        for r in rows:
            lines.append(f"  [{r['priority']}] {r['ticket_id']}: {r['summary']} — {r['status']}")
        return "\n".join(lines)
    finally:
        conn.close()


# ========== FINANCE TOOLS ==========

@tool
def get_expense_status(claim_id: str) -> str:
    """Check the status of an expense reimbursement claim by its ID (e.g. EXP-2026-0001)."""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT ec.*, e.full_name, r.full_name AS reviewer_name "
            "FROM expense_claims ec "
            "JOIN employees e ON ec.employee_id = e.employee_id "
            "LEFT JOIN employees r ON ec.reviewed_by = r.employee_id "
            "WHERE ec.claim_id = ?",
            (claim_id.upper(),),
        ).fetchone()
        if not row:
            return f"Expense claim '{claim_id}' not found."

        lines = [
            f"Expense Claim {row['claim_id']}:",
            f"  Employee:    {row['full_name']}",
            f"  Amount:      {row['currency']} {row['amount']:,.2f}",
            f"  Category:    {row['category']}",
            f"  Description: {row['description']}",
            f"  Receipts:    {row['receipt_count']}",
            f"  Status:      {row['status']}",
            f"  Submitted:   {row['submitted_at']}",
        ]
        if row["reviewed_by"]:
            lines.append(f"  Reviewed by: {row['reviewer_name']} on {row['reviewed_at']}")
        if row["rejection_reason"]:
            lines.append(f"  Rejection reason: {row['rejection_reason']}")
        if row["paid_at"]:
            lines.append(f"  Paid on:     {row['paid_at']}")
        return "\n".join(lines)
    finally:
        conn.close()


@tool
def submit_expense_claim(employee_id: str, amount: float, category: str, description: str, receipt_count: int = 1) -> str:
    """Submit a new expense reimbursement claim. category: travel/meals/software/hardware/training/office_supplies/other."""
    valid_categories = ("travel", "meals", "software", "hardware", "training", "office_supplies", "other")
    if category not in valid_categories:
        return f"Invalid category '{category}'. Must be one of: {', '.join(valid_categories)}"
    if amount <= 0:
        return "Amount must be positive."

    conn = _get_db()
    try:
        emp = conn.execute("SELECT 1 FROM employees WHERE employee_id = ? AND is_active = 1", (employee_id,)).fetchone()
        if not emp:
            return f"Employee '{employee_id}' not found."

        claim_id = _next_claim_id(conn)
        conn.execute(
            "INSERT INTO expense_claims (claim_id, employee_id, amount, category, description, receipt_count, status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'submitted')",
            (claim_id, employee_id, amount, category, description, receipt_count),
        )
        conn.commit()

        return (
            f"Expense claim submitted!\n"
            f"  Claim ID:    {claim_id}\n"
            f"  Amount:      INR {amount:,.2f}\n"
            f"  Category:    {category}\n"
            f"  Description: {description}\n"
            f"  Status:      submitted (pending finance review)"
        )
    finally:
        conn.close()


@tool
def get_payslip(employee_id: str, month: str) -> str:
    """Retrieve salary slip details for a given month (format: YYYY-MM, e.g. 2026-01)."""
    try:
        datetime.strptime(month, "%Y-%m")
    except ValueError:
        return "Invalid month format. Use YYYY-MM (e.g. 2026-01)."

    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT p.*, e.full_name, e.designation FROM payslips p "
            "JOIN employees e ON p.employee_id = e.employee_id "
            "WHERE p.employee_id = ? AND p.month = ?",
            (employee_id, month),
        ).fetchone()
        if not row:
            # Check if the month is in the future
            now = datetime.now()
            req_month = datetime.strptime(month, "%Y-%m")
            if req_month.replace(day=1) >= now.replace(day=1):
                return f"Salary slip for {month} is not yet available. Slips are generated by the 5th of the following month."
            return f"No salary slip found for '{employee_id}' for {month}. Please contact HR if this is an error."

        return (
            f"Salary Slip — {row['full_name']} ({row['designation']})\n"
            f"  Month:       {row['month']}\n"
            f"  Gross Salary: INR {row['gross_salary']:,.2f}\n"
            f"  Deductions:   INR {row['deductions']:,.2f}\n"
            f"  Net Salary:   INR {row['net_salary']:,.2f}\n"
            f"  Status:       {row['status']}\n"
            f"  Generated:    {row['generated_at']}"
        )
    finally:
        conn.close()


# ========== FACILITIES TOOLS ==========

@tool
def check_room_availability(date: str) -> str:
    """Check meeting room availability for a given date (YYYY-MM-DD format). Shows all rooms and their bookings."""
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return "Invalid date format. Use YYYY-MM-DD."

    conn = _get_db()
    try:
        rooms = conn.execute(
            "SELECT * FROM meeting_rooms WHERE is_active = 1 ORDER BY floor, room_name"
        ).fetchall()
        bookings = conn.execute(
            "SELECT rb.*, e.full_name FROM room_bookings rb "
            "JOIN employees e ON rb.booked_by = e.employee_id "
            "WHERE rb.date = ? AND rb.status = 'confirmed' ORDER BY rb.room_id, rb.start_time",
            (date,),
        ).fetchall()

        booking_map: dict[str, list] = {}
        for b in bookings:
            booking_map.setdefault(b["room_id"], []).append(b)

        lines = [f"Meeting room availability for {date}:\n"]
        for room in rooms:
            rid = room["room_id"]
            features = []
            if room["has_projector"]:
                features.append("projector")
            if room["has_whiteboard"]:
                features.append("whiteboard")
            if room["has_video_conf"]:
                features.append("video conf")
            feat_str = f" [{', '.join(features)}]" if features else ""
            lines.append(
                f"  {room['room_name']} — {room['floor']}, capacity {room['capacity']}{feat_str}:"
            )

            room_bookings = booking_map.get(rid, [])
            if room_bookings:
                for b in room_bookings:
                    lines.append(
                        f"    {b['start_time']}-{b['end_time']}: {b['purpose']} "
                        f"({b['full_name']}, {b['attendees']} attendees)"
                    )
            else:
                lines.append("    Available all day")
        return "\n".join(lines)
    finally:
        conn.close()


@tool
def book_meeting_room(room_name: str, date: str, start_time: str, end_time: str, booked_by: str, purpose: str = "Meeting", attendees: int = 2) -> str:
    """Book a meeting room. room_name: e.g. 'Ganges'. Date: YYYY-MM-DD. Times: HH:MM (24h). booked_by: employee_id."""
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return "Invalid date format. Use YYYY-MM-DD."

    conn = _get_db()
    try:
        # Find room by name (case-insensitive)
        room = conn.execute(
            "SELECT * FROM meeting_rooms WHERE LOWER(room_name) = LOWER(?) AND is_active = 1",
            (room_name,),
        ).fetchone()
        if not room:
            available = [r["room_name"] for r in conn.execute(
                "SELECT room_name FROM meeting_rooms WHERE is_active = 1"
            ).fetchall()]
            return f"Room '{room_name}' not found. Available rooms: {', '.join(available)}"

        # Validate capacity
        if attendees > room["capacity"]:
            return (
                f"Room '{room['room_name']}' has capacity for {room['capacity']} people, "
                f"but {attendees} attendees requested. Try a larger room."
            )

        # Verify employee exists
        emp = conn.execute("SELECT 1 FROM employees WHERE employee_id = ?", (booked_by,)).fetchone()
        if not emp:
            return f"Employee '{booked_by}' not found."

        # Check for time conflicts (only confirmed bookings)
        conflicts = conn.execute(
            "SELECT rb.*, e.full_name FROM room_bookings rb "
            "JOIN employees e ON rb.booked_by = e.employee_id "
            "WHERE rb.room_id = ? AND rb.date = ? AND rb.status = 'confirmed' "
            "AND NOT (rb.end_time <= ? OR rb.start_time >= ?)",
            (room["room_id"], date, start_time, end_time),
        ).fetchall()
        if conflicts:
            conflict_info = "; ".join(
                f"{c['start_time']}-{c['end_time']} by {c['full_name']}" for c in conflicts
            )
            return (
                f"Room '{room['room_name']}' has a conflict on {date}: {conflict_info}. "
                f"Please choose another time slot or room."
            )

        conn.execute(
            "INSERT INTO room_bookings (room_id, date, start_time, end_time, booked_by, purpose, attendees) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (room["room_id"], date, start_time, end_time, booked_by, purpose, attendees),
        )
        conn.commit()

        return (
            f"Room booked successfully!\n"
            f"  Room:      {room['room_name']} ({room['floor']}, capacity: {room['capacity']})\n"
            f"  Date:      {date}\n"
            f"  Time:      {start_time} - {end_time}\n"
            f"  Booked by: {booked_by}\n"
            f"  Purpose:   {purpose}\n"
            f"  Attendees: {attendees}"
        )
    finally:
        conn.close()


# ========== ANALYTICS TOOLS ==========

def _get_history_db() -> sqlite3.Connection:
    """Get a read-only connection to the conversation history database."""
    conn = sqlite3.connect(HISTORY_DB)
    conn.row_factory = sqlite3.Row
    return conn


@tool
def get_conversation_stats(period: str = "all") -> str:
    """Get conversation volume and quality metrics. period: 'today', 'week', 'month', or 'all'."""
    # Whitelist valid periods — no user input reaches SQL
    _PERIOD_FILTERS = {
        "today": "AND date(created_at) = date('now')",
        "week":  "AND date(created_at) >= date('now', '-7 days')",
        "month": "AND date(created_at) >= date('now', '-30 days')",
        "all":   "",
    }
    if period not in _PERIOD_FILTERS:
        period = "all"
    date_filter = _PERIOD_FILTERS[period]

    conn = _get_history_db()
    try:
        # Total messages and unique users
        row = conn.execute(
            "SELECT COUNT(*) as total, COUNT(DISTINCT email) as users "
            "FROM messages WHERE 1=1 " + date_filter
        ).fetchone()
        total = row["total"]
        users = row["users"]

        # Quality metrics from assistant messages
        stats = conn.execute(
            "SELECT COUNT(*) as cnt, "
            "SUM(CASE WHEN escalated=1 THEN 1 ELSE 0 END) as escalated, "
            "SUM(CASE WHEN fallback_used=1 THEN 1 ELSE 0 END) as fallbacks, "
            "AVG(confidence) as avg_conf "
            "FROM messages WHERE role='assistant' " + date_filter
        ).fetchone()
        responses = stats["cnt"] or 0
        escalated = stats["escalated"] or 0
        fallbacks = stats["fallbacks"] or 0
        avg_conf = stats["avg_conf"] or 0

        esc_rate = (escalated / responses * 100) if responses else 0
        fb_rate = (fallbacks / responses * 100) if responses else 0

        # Category breakdown
        cats = conn.execute(
            "SELECT category, COUNT(*) as cnt FROM messages "
            "WHERE role='assistant' AND COALESCE(category, '') != '' " + date_filter +
            " GROUP BY category ORDER BY cnt DESC"
        ).fetchall()
        cat_lines = [f"  {c['category']}: {c['cnt']}" for c in cats]

        lines = [
            f"Conversation Stats ({period}):",
            f"  Total messages: {total:,}",
            f"  Unique users: {users:,}",
            f"  AI responses: {responses:,}",
            f"  Escalation rate: {esc_rate:.1f}%",
            f"  Fallback rate: {fb_rate:.1f}%",
            f"  Avg confidence: {avg_conf:.1f}/10",
            f"\nCategory breakdown:",
        ] + (cat_lines or ["  No data"])
        return "\n".join(lines)
    finally:
        conn.close()


@tool
def get_ticket_summary() -> str:
    """Get a summary of all IT support tickets — counts by status and priority, plus top open tickets."""
    conn = _get_db()
    try:
        by_status = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM tickets GROUP BY status ORDER BY cnt DESC"
        ).fetchall()
        by_priority = conn.execute(
            "SELECT priority, COUNT(*) as cnt FROM tickets WHERE status NOT IN ('Closed','Resolved') "
            "GROUP BY priority ORDER BY priority"
        ).fetchall()
        open_tickets = conn.execute(
            "SELECT ticket_id, summary, priority, status, created_at FROM tickets "
            "WHERE status NOT IN ('Closed','Resolved') "
            "ORDER BY CASE priority WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 WHEN 'P3' THEN 3 ELSE 4 END LIMIT 10"
        ).fetchall()

        lines = ["Ticket Summary:", "\nBy status:"]
        for r in by_status:
            lines.append(f"  {r['status']}: {r['cnt']}")
        lines.append("\nOpen tickets by priority:")
        for r in by_priority:
            lines.append(f"  {r['priority']}: {r['cnt']}")
        lines.append(f"\nTop open tickets ({len(open_tickets)}):")
        for t in open_tickets:
            lines.append(f"  [{t['priority']}] {t['ticket_id']}: {t['summary']} — {t['status']}")
        return "\n".join(lines)
    finally:
        conn.close()


@tool
def get_expense_summary() -> str:
    """Get expense claims summary — status breakdown and pending claims list."""
    conn = _get_db()
    try:
        by_status = conn.execute(
            "SELECT status, COUNT(*) as cnt, SUM(amount) as total FROM expense_claims GROUP BY status"
        ).fetchall()
        pending = conn.execute(
            "SELECT ec.claim_id, e.full_name, ec.amount, ec.category, ec.status, ec.submitted_at "
            "FROM expense_claims ec JOIN employees e ON ec.employee_id = e.employee_id "
            "WHERE ec.status IN ('submitted','under_review') ORDER BY ec.submitted_at"
        ).fetchall()

        lines = ["Expense Claims Summary:", "\nBy status:"]
        for r in by_status:
            total = r['total'] or 0
            lines.append(f"  {r['status']}: {r['cnt']} claims, INR {total:,.2f}")
        lines.append(f"\nPending claims ({len(pending)}):")
        for p in pending:
            lines.append(f"  {p['claim_id']}: {p['full_name']} — INR {p['amount']:,.2f} ({p['category']}) — {p['status']}")
        return "\n".join(lines)
    finally:
        conn.close()


@tool
def get_leave_summary() -> str:
    """Get a summary of pending leave requests across the organization."""
    conn = _get_db()
    try:
        pending = conn.execute(
            "SELECT lr.*, e.full_name FROM leave_requests lr "
            "JOIN employees e ON lr.employee_id = e.employee_id "
            "WHERE lr.status = 'pending' ORDER BY lr.start_date"
        ).fetchall()
        lines = [f"Pending Leave Requests ({len(pending)}):"]
        if not pending:
            lines.append("  No pending leave requests.")
        for p in pending:
            lines.append(
                f"  {p['full_name']}: {p['leave_type']} leave, "
                f"{p['start_date']} to {p['end_date']} ({p['days']} days) — {p['reason']}"
            )
        return "\n".join(lines)
    finally:
        conn.close()


@tool
def get_room_utilization() -> str:
    """Get meeting room booking counts for the last 7 days."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT mr.room_name, COUNT(rb.id) as bookings "
            "FROM meeting_rooms mr "
            "LEFT JOIN room_bookings rb ON mr.room_id = rb.room_id "
            "  AND rb.status = 'confirmed' "
            "  AND rb.date >= date('now', '-7 days') AND rb.date <= date('now', '+1 day') "
            "WHERE mr.is_active = 1 "
            "GROUP BY mr.room_id ORDER BY bookings DESC"
        ).fetchall()
        lines = ["Room Utilization (last 7 days):"]
        for r in rows:
            lines.append(f"  {r['room_name']}: {r['bookings']} bookings")
        return "\n".join(lines)
    finally:
        conn.close()


ANALYTICS_TOOLS = [get_conversation_stats, get_ticket_summary, get_expense_summary, get_leave_summary, get_room_utilization]


# ========== ACCOUNT TOOLS ==========

@tool
def change_my_password(current_password: str, new_password: str) -> str:
    """Change the logged-in user's password. Requires their current password for verification."""
    from auth import current_user_email, verify_password, set_user_password, get_user_password

    try:
        email = current_user_email.get()
    except LookupError:
        return "Unable to determine your identity. Please log out and log back in."

    if len(new_password) < 8:
        return "New password must be at least 8 characters long."

    if current_password == new_password:
        return "New password must be different from your current password."

    user = get_user_password(email)
    if not user:
        return "No password record found. Please log out and log back in to initialize your account."

    if not verify_password(current_password, user["password_hash"], user["password_salt"]):
        return "Current password is incorrect. Please try again."

    set_user_password(email, new_password)
    return "Password changed successfully! Please use your new password next time you log in."


ACCOUNT_TOOLS = [change_my_password]


# ========== LLM CONFIG TOOLS (skill_admin) ==========

GROQ_MODELS = {
    "llama-3.3-70b-versatile",
    "llama-3.1-70b-versatile",
    "llama-3.1-8b-instant",
    "llama3-70b-8192",
    "llama3-8b-8192",
    "mixtral-8x7b-32768",
    "gemma2-9b-it",
}

# OpenRouter supports hundreds of models; validate format only (provider/model)
def _is_valid_openrouter_model(name: str) -> bool:
    return "/" in name and len(name) > 3


def _get_system_config(key: str) -> str:
    """Read a single value from system_config table."""
    conn = _get_db()
    try:
        row = conn.execute("SELECT value FROM system_config WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else ""
    finally:
        conn.close()


def _set_system_config(key: str, value: str, updated_by: str = "") -> None:
    """Write a single value to system_config table."""
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO system_config (key, value, updated_by, updated_at) "
            "VALUES (?, ?, ?, datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_by=excluded.updated_by, updated_at=datetime('now')",
            (key, value, updated_by),
        )
        conn.commit()
    finally:
        conn.close()


def _mask_api_key(key: str) -> str:
    """Show first 4 + last 3 chars of an API key."""
    if not key or len(key) < 10:
        return "***" if key else ""
    return f"{key[:4]}...{key[-3:]}"


def _get_fernet():
    """Derive a deterministic Fernet key from SECRET_KEY via PBKDF2."""
    from cryptography.fernet import Fernet
    secret = os.getenv("SECRET_KEY", "frontdeskai-dev-secret")
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        secret.encode(),
        b"frontdeskai-smtp-v1",
        100_000,
    )
    return Fernet(base64.urlsafe_b64encode(dk[:32]))


def _encrypt_smtp_password(plaintext: str) -> str:
    """Encrypt an SMTP password with Fernet."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def _decrypt_smtp_password(ciphertext: str) -> str:
    """Decrypt an SMTP password with Fernet."""
    return _get_fernet().decrypt(ciphertext.encode()).decode()


def _encrypt_value(plaintext: str) -> str:
    """Encrypt any string with Fernet for storing secrets in system_config."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def _decrypt_value(ciphertext: str) -> str:
    """Decrypt a Fernet-encrypted value from system_config."""
    return _get_fernet().decrypt(ciphertext.encode()).decode()


@tool
def get_llm_config() -> str:
    """Get the current LLM configuration (primary and fallback)."""
    provider = _get_system_config("llm_provider") or "ollama"
    model = _get_system_config("llm_model") or "llama3.3:70b"
    temp = _get_system_config("llm_temperature") or "0"
    api_key = _get_system_config("llm_api_key") or ""
    key_status = f"custom key configured ({_mask_api_key(api_key)})" if api_key else "using environment variable"

    fb_provider = _get_system_config("llm_fallback_provider") or ""
    fb_model = _get_system_config("llm_fallback_model") or ""
    fb_api_key = _get_system_config("llm_fallback_api_key") or ""

    fb_section = (
        f"\nFallback LLM (used when primary hits rate limits or errors):\n"
        f"  Provider:    {fb_provider}\n"
        f"  Model:       {fb_model}\n"
        f"  API Key:     {'custom key configured (' + _mask_api_key(fb_api_key) + ')' if fb_api_key else 'using environment variable'}"
    ) if fb_model else "\nFallback LLM: not configured (say 'configure fallback LLM' to set one)"

    return (
        f"Current LLM Configuration:\n"
        f"  Provider:    {provider}\n"
        f"  Model:       {model}\n"
        f"  Temperature: {temp}\n"
        f"  API Key:     {key_status}"
        f"{fb_section}"
    )


@tool
def change_llm_model(model_name: str, provider: str = "groq", temperature: float = 0.0, api_key: str = "") -> str:
    """Change the LLM model used by all agents. provider: 'groq', 'openrouter', or 'ollama'.
    model_name: e.g. 'llama-3.1-8b-instant' (groq), 'google/gemini-2.0-flash-001' (openrouter), or 'llama3.3:70b' (ollama).
    api_key: optional — omit to keep using the environment variable (GROQ_API_KEY / OLLAMA_API_KEY).
    For openrouter and ollama, an API key is required (set via this tool or the respective env var)."""
    from auth import current_user_email
    try:
        email = current_user_email.get()
    except LookupError:
        email = "unknown"

    provider = provider.lower().strip()
    if provider not in ("groq", "openrouter", "ollama"):
        return f"Invalid provider '{provider}'. Must be 'groq', 'openrouter', or 'ollama'."

    if provider == "groq" and model_name not in GROQ_MODELS:
        valid = ", ".join(sorted(GROQ_MODELS))
        return f"Invalid Groq model '{model_name}'. Valid models: {valid}"

    if provider == "openrouter" and not _is_valid_openrouter_model(model_name):
        return (
            f"Invalid OpenRouter model '{model_name}'. "
            "OpenRouter models use the format 'provider/model' (e.g. 'google/gemini-2.0-flash-001', 'anthropic/claude-3.5-sonnet')."
        )

    # For openrouter, an API key is required (either passed or from env)
    if provider == "openrouter" and not api_key and not os.getenv("OPENROUTER_API_KEY"):
        return "OpenRouter requires an API key. Pass one via api_key parameter or set OPENROUTER_API_KEY env var."

    if provider == "ollama" and not api_key and not os.getenv("OLLAMA_API_KEY"):
        return "Ollama Cloud requires an API key. Pass one via api_key parameter or set OLLAMA_API_KEY env var."

    _set_system_config("llm_provider", provider, email)
    _set_system_config("llm_model", model_name, email)
    _set_system_config("llm_temperature", str(temperature), email)
    if api_key:
        _set_system_config("llm_api_key", api_key, email)

    # Reload the LLM config in agents module
    from agents import reload_llm_config
    reload_llm_config()

    return (
        f"LLM configuration updated successfully!\n"
        f"  Provider:    {provider}\n"
        f"  Model:       {model_name}\n"
        f"  Temperature: {temperature}\n"
        f"  API Key:     {'custom key set' if api_key else 'unchanged (using env var)'}\n"
        f"Changes take effect immediately for all subsequent requests."
    )


@tool
def configure_fallback_llm(model_name: str, provider: str = "ollama", api_key: str = "") -> str:
    """Configure a fallback LLM used automatically when the primary hits rate limits or errors.
    model_name: e.g. 'llama3.3:70b' (ollama), 'llama-3.1-8b-instant' (groq), or 'google/gemini-flash-1.5' (openrouter).
    provider: 'ollama', 'groq', or 'openrouter'. api_key: optional, leave empty to use env var (OLLAMA_API_KEY).
    To disable the fallback, call with model_name='none'."""
    from auth import current_user_email
    try:
        email = current_user_email.get()
    except LookupError:
        email = "unknown"

    if model_name.lower() == "none":
        _set_system_config("llm_fallback_provider", "", email)
        _set_system_config("llm_fallback_model", "", email)
        _set_system_config("llm_fallback_api_key", "", email)
        from agents import reload_llm_config
        reload_llm_config()
        return "Fallback LLM disabled."

    provider = provider.lower().strip()
    if provider not in ("groq", "openrouter", "ollama"):
        return f"Invalid provider '{provider}'. Must be 'groq', 'openrouter', or 'ollama'."

    if provider == "groq" and model_name not in GROQ_MODELS:
        valid = ", ".join(sorted(GROQ_MODELS))
        return f"Invalid Groq model '{model_name}'. Valid models: {valid}"

    if provider == "openrouter" and not _is_valid_openrouter_model(model_name):
        return (
            f"Invalid OpenRouter model format '{model_name}'. "
            "Use 'provider/model' format (e.g. 'google/gemini-flash-1.5', 'anthropic/claude-3.5-haiku')."
        )

    if provider == "openrouter" and not api_key and not os.getenv("OPENROUTER_API_KEY"):
        return "OpenRouter requires an API key. Pass one via api_key parameter or set OPENROUTER_API_KEY env var."

    if provider == "ollama" and not api_key and not os.getenv("OLLAMA_API_KEY"):
        return "Ollama Cloud requires an API key. Pass one via api_key parameter or set OLLAMA_API_KEY env var."

    _set_system_config("llm_fallback_provider", provider, email)
    _set_system_config("llm_fallback_model", model_name, email)
    _set_system_config("llm_fallback_temperature", "0", email)
    if api_key:
        _set_system_config("llm_fallback_api_key", api_key, email)

    from agents import reload_llm_config
    reload_llm_config()

    return (
        f"Fallback LLM configured!\n"
        f"  Provider: {provider}\n"
        f"  Model:    {model_name}\n"
        f"  API Key:  {'custom key set' if api_key else 'using env var'}\n"
        f"The fallback will be used automatically when the primary LLM hits rate limits or errors."
    )


LLM_CONFIG_TOOLS = [get_llm_config, change_llm_model, configure_fallback_llm]


# ========== SMTP / EMAIL TOOLS (skill_admin) ==========

@tool
def configure_smtp(host: str, port: int, username: str, password: str, from_email: str, use_tls: bool = True) -> str:
    """Configure SMTP email settings (e.g. AWS SES, Gmail). Admin only.
    host: SMTP server hostname. port: SMTP port (587 for STARTTLS, 465 for SSL).
    username: SMTP auth username. password: SMTP auth password (will be encrypted).
    from_email: Sender email address. use_tls: Whether to use TLS (default true)."""
    from auth import current_user_email

    try:
        email = current_user_email.get()
    except LookupError:
        email = "unknown"

    # Validate inputs
    if not host or not host.strip():
        return "SMTP host is required."
    if port not in (25, 465, 587, 2525):
        return f"Invalid SMTP port {port}. Common ports: 587 (STARTTLS), 465 (SSL), 25, 2525."
    if not username or not username.strip():
        return "SMTP username is required."
    if not password or not password.strip():
        return "SMTP password is required."
    if not from_email or "@" not in from_email:
        return "A valid from_email address is required (must contain @)."

    # Encrypt password
    encrypted_password = _encrypt_smtp_password(password)

    # Store all SMTP config
    _set_system_config("smtp_host", host.strip(), email)
    _set_system_config("smtp_port", str(port), email)
    _set_system_config("smtp_username", username.strip(), email)
    _set_system_config("smtp_password_enc", encrypted_password, email)
    _set_system_config("smtp_from_email", from_email.strip(), email)
    _set_system_config("smtp_use_tls", "true" if use_tls else "false", email)

    return (
        f"SMTP configured successfully!\n"
        f"  Host:       {host.strip()}\n"
        f"  Port:       {port}\n"
        f"  Username:   {_mask_api_key(username.strip())}\n"
        f"  Password:   *** (encrypted)\n"
        f"  From Email: {from_email.strip()}\n"
        f"  TLS:        {'enabled' if use_tls else 'disabled'}\n"
        f"  Updated by: {email}"
    )


@tool
def get_smtp_config() -> str:
    """Show the current SMTP email configuration (password is masked)."""
    host = _get_system_config("smtp_host")
    port = _get_system_config("smtp_port") or "587"
    username = _get_system_config("smtp_username")
    password_enc = _get_system_config("smtp_password_enc")
    from_email = _get_system_config("smtp_from_email")
    use_tls = _get_system_config("smtp_use_tls") or "true"

    if not host:
        return (
            "SMTP is not configured.\n"
            "Ask an admin to configure it with: configure SMTP host, port, username, password, and from_email.\n"
            "Example: 'Configure SMTP with host=email-smtp.us-east-1.amazonaws.com port=587 "
            "username=AKIA... password=... from=noreply@example.com'"
        )

    return (
        f"Current SMTP Configuration:\n"
        f"  Host:       {host}\n"
        f"  Port:       {port}\n"
        f"  Username:   {_mask_api_key(username)}\n"
        f"  Password:   {'*** (encrypted)' if password_enc else 'not set'}\n"
        f"  From Email: {from_email}\n"
        f"  TLS:        {'enabled' if use_tls == 'true' else 'disabled'}"
    )


@tool
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email using the configured SMTP settings. Admin only.
    to: Recipient email address. subject: Email subject line. body: Email body text."""
    from auth import current_user_email

    try:
        sender_identity = current_user_email.get()
    except LookupError:
        sender_identity = "unknown"

    # Validate recipient
    if not to or "@" not in to:
        return "Invalid recipient email address (must contain @)."

    # Validate subject/body length
    if not subject or len(subject.strip()) == 0:
        return "Email subject is required."
    if len(subject) > 500:
        return "Email subject too long (max 500 characters)."
    if not body or len(body.strip()) == 0:
        return "Email body is required."
    if len(body) > 50000:
        return "Email body too long (max 50,000 characters)."

    # Load SMTP config
    host = _get_system_config("smtp_host")
    if not host:
        return "SMTP is not configured. Please configure SMTP settings first."

    port = int(_get_system_config("smtp_port") or "587")
    username = _get_system_config("smtp_username")
    password_enc = _get_system_config("smtp_password_enc")
    from_email = _get_system_config("smtp_from_email")
    use_tls = (_get_system_config("smtp_use_tls") or "true") == "true"

    if not password_enc:
        return "SMTP password is not configured. Please run configure_smtp first."

    # Decrypt password
    try:
        password = _decrypt_smtp_password(password_enc)
    except Exception:
        return (
            "Failed to decrypt SMTP password. This can happen if SECRET_KEY was rotated. "
            "Please re-run configure_smtp to set a new password."
        )

    # Build email message
    msg = MIMEMultipart()
    msg["From"] = from_email
    msg["To"] = to.strip()
    msg["Subject"] = subject.strip()
    msg.attach(MIMEText(body, "plain"))

    # Send email
    try:
        if port == 465 and not use_tls:
            # Direct SSL connection
            with smtplib.SMTP_SSL(host, port, timeout=30) as server:
                server.login(username, password)
                server.send_message(msg)
        else:
            # STARTTLS (port 587 default)
            with smtplib.SMTP(host, port, timeout=30) as server:
                if use_tls:
                    server.starttls()
                server.login(username, password)
                server.send_message(msg)

        return (
            f"Email sent successfully!\n"
            f"  To:      {to.strip()}\n"
            f"  From:    {from_email}\n"
            f"  Subject: {subject.strip()}\n"
            f"  Sent by: {sender_identity}"
        )
    except smtplib.SMTPAuthenticationError:
        return "SMTP authentication failed. Please check the SMTP username and password."
    except smtplib.SMTPRecipientsRefused:
        return f"Recipient '{to}' was refused by the SMTP server. Please check the email address."
    except smtplib.SMTPException as e:
        return f"SMTP error: {e}"
    except OSError as e:
        return f"Network error connecting to SMTP server: {e}"


SMTP_TOOLS = [configure_smtp, get_smtp_config, send_email]


# ========== FILE TOOLS (for skill_admin — restricted to /shared/) ==========

_SHARED_DIR = os.path.dirname(os.getenv("SQLITE_DIR", "/shared/.sqlite"))


@tool
def read_local_file(file_path: str) -> str:
    """Read a file from the /shared/ directory. Path must be under /shared/."""
    resolved = os.path.realpath(file_path)
    if not resolved.startswith(os.path.realpath(_SHARED_DIR)):
        return f"Access denied: can only read files under {_SHARED_DIR}"
    if not os.path.isfile(resolved):
        return f"File not found: {file_path}"
    try:
        with open(resolved, encoding="utf-8") as f:
            content = f.read()
        if len(content) > 50000:
            content = content[:50000] + "\n... (truncated)"
        return content
    except Exception as e:
        return f"Error reading file: {e}"


@tool
def write_local_file(file_path: str, content: str) -> str:
    """Write content to a file in the /shared/ directory. Path must be under /shared/."""
    resolved = os.path.realpath(os.path.join(_SHARED_DIR, file_path) if not file_path.startswith("/") else file_path)
    if not resolved.startswith(os.path.realpath(_SHARED_DIR)):
        return f"Access denied: can only write files under {_SHARED_DIR}"
    try:
        os.makedirs(os.path.dirname(resolved), exist_ok=True)
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(content)
        return f"File written successfully: {resolved} ({len(content)} bytes)"
    except Exception as e:
        return f"Error writing file: {e}"


FILE_TOOLS = [read_local_file, write_local_file]


# ========== TOOL REGISTRY ==========

HR_TOOLS = [get_leave_balance, apply_leave]
TECH_TOOLS = [create_ticket, get_ticket_status, list_my_tickets]
FINANCE_TOOLS = [get_expense_status, submit_expense_claim, get_payslip]
FACILITIES_TOOLS = [check_room_availability, book_meeting_room]

from skills import SKILL_ADMIN_TOOLS

# Append LLM config and SMTP tools to skill_admin tools
SKILL_ADMIN_TOOLS = SKILL_ADMIN_TOOLS + LLM_CONFIG_TOOLS + SMTP_TOOLS + FILE_TOOLS

DOMAIN_TOOLS = {
    "hr": HR_TOOLS,
    "tech": TECH_TOOLS,
    "finance": FINANCE_TOOLS,
    "facilities": FACILITIES_TOOLS,
    "analytics": ANALYTICS_TOOLS,
    "account": ACCOUNT_TOOLS,
    "skill_admin": SKILL_ADMIN_TOOLS,
}
