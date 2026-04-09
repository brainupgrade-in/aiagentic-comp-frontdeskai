"""HR Leave MCP Server.

Exposes employee leave balance and usage as MCP tools backed by PostgreSQL.
Transport: streamable-http on port 8001 — reachable cross-namespace via:
  http://mcp-leave.postgres.svc.cluster.local:8001/mcp
"""

import os
import psycopg2
import psycopg2.extras
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

mcp = FastMCP("HR Leave Service")

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

PG_HOST     = os.getenv("PG_HOST",     "postgres.postgres.svc.cluster.local")
PG_PORT     = int(os.getenv("PG_PORT", "5432"))
PG_DB       = os.getenv("PG_DB",       "hrdb")
PG_USER     = os.getenv("PG_USER",     "hruser")
PG_PASSWORD = os.getenv("PG_PASSWORD", "hrpassword")


def _conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DB,
        user=PG_USER,
        password=PG_PASSWORD,
        cursor_factory=psycopg2.extras.RealDictCursor,
        connect_timeout=5,
    )


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

LEAVE_DEFAULTS = {
    "casual_leave": 12,
    "sick_leave":    6,
    "earned_leave": 15,
    "wfh_days":     24,
}


def _ensure_employee(cur, employee_id: str) -> bool:
    """Create employee + leave_balance rows with defaults if they don't exist.

    Returns True if records were just created, False if they already existed.
    """
    cur.execute("SELECT employee_id FROM employees WHERE employee_id = %s", (employee_id,))
    if not cur.fetchone():
        full_name   = employee_id.replace(".", " ").replace("_", " ").title()
        email       = f"{employee_id}@unigps.in"
        cur.execute(
            """
            INSERT INTO employees (employee_id, full_name, email, department, designation)
            VALUES (%s, %s, %s, 'General', 'Employee')
            ON CONFLICT (employee_id) DO NOTHING
            """,
            (employee_id, full_name, email),
        )

    cur.execute("SELECT employee_id FROM leave_balances WHERE employee_id = %s", (employee_id,))
    if not cur.fetchone():
        import datetime
        cur.execute(
            """
            INSERT INTO leave_balances
                (employee_id, casual_leave, sick_leave, earned_leave, wfh_days, year)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (employee_id) DO NOTHING
            """,
            (
                employee_id,
                LEAVE_DEFAULTS["casual_leave"],
                LEAVE_DEFAULTS["sick_leave"],
                LEAVE_DEFAULTS["earned_leave"],
                LEAVE_DEFAULTS["wfh_days"],
                datetime.date.today().year,
            ),
        )
        return True
    return False


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def get_leave_balance(employee_id: str) -> str:
    """Return remaining leave balance for an employee.

    If the employee has no record in the HR database, one is created
    automatically with standard annual leave defaults.

    Args:
        employee_id: Username portion of the employee email
                     (e.g. 'alice' for alice@unigps.in).
    """
    conn = _conn()
    try:
        cur = conn.cursor()

        # Auto-provision if missing
        created = _ensure_employee(cur, employee_id)
        if created:
            conn.commit()

        # Current balance
        cur.execute(
            """
            SELECT e.full_name, e.department,
                   lb.casual_leave, lb.sick_leave, lb.earned_leave,
                   lb.wfh_days, lb.year
            FROM   employees e
            JOIN   leave_balances lb ON lb.employee_id = e.employee_id
            WHERE  e.employee_id = %s AND e.is_active = TRUE
            """,
            (employee_id,),
        )
        emp = cur.fetchone()
        if not emp:
            return f"Could not create leave record for '{employee_id}'. Please contact HR."

        # Approved usage this year
        cur.execute(
            """
            SELECT leave_type, SUM(days) AS used
            FROM   leave_requests
            WHERE  employee_id = %s
              AND  status = 'approved'
              AND  EXTRACT(YEAR FROM start_date) = %s
            GROUP  BY leave_type
            """,
            (employee_id, emp["year"]),
        )
        used = {r["leave_type"]: r["used"] for r in cur.fetchall()}

        # Pending requests
        cur.execute(
            """
            SELECT leave_type, start_date, end_date, days
            FROM   leave_requests
            WHERE  employee_id = %s AND status = 'pending'
            ORDER  BY start_date
            """,
            (employee_id,),
        )
        pending = cur.fetchall()

        header = f"Leave balance for {emp['full_name']} ({emp['department']}) — {emp['year']}:"
        if created:
            header += "  [account auto-provisioned with standard defaults]"
        lines = [
            header,
            f"  Casual Leave : {emp['casual_leave']} days remaining  (used: {used.get('casual', 0)})",
            f"  Sick Leave   : {emp['sick_leave']} days remaining  (used: {used.get('sick', 0)})",
            f"  Earned Leave : {emp['earned_leave']} days remaining  (used: {used.get('earned', 0)})",
            f"  WFH Days     : {emp['wfh_days']} days remaining  (used: {used.get('wfh', 0)})",
        ]
        if pending:
            lines.append("\nPending requests:")
            for p in pending:
                lines.append(
                    f"  • {p['leave_type'].title()} — "
                    f"{p['start_date']} to {p['end_date']} ({p['days']} days)"
                )
        return "\n".join(lines)
    finally:
        conn.close()


@mcp.tool()
def approve_leave(
    employee_id: str,
    leave_type: str,
    start_date: str,
    end_date: str,
    reason: str = "",
) -> str:
    """Approve a leave request and record it in the HR database.

    The HR agent calls this tool after deciding the request is valid.
    It auto-provisions the employee if they have no record, validates
    that sufficient balance exists, then inserts an approved leave_request
    row and deducts the days from leave_balances — all in one transaction.

    Args:
        employee_id: Username portion of the employee email.
        leave_type:  One of: casual, sick, earned, wfh.
        start_date:  YYYY-MM-DD format.
        end_date:    YYYY-MM-DD format.
        reason:      Optional reason for the leave.
    """
    import datetime

    valid_types = ("casual", "sick", "earned", "wfh")
    if leave_type not in valid_types:
        return f"Invalid leave type '{leave_type}'. Must be one of: {', '.join(valid_types)}."

    try:
        start = datetime.date.fromisoformat(start_date)
        end   = datetime.date.fromisoformat(end_date)
    except ValueError:
        return "Invalid date format. Use YYYY-MM-DD."

    if end < start:
        return "End date cannot be before start date."

    days = (end - start).days + 1

    balance_col = {
        "casual":  "casual_leave",
        "sick":    "sick_leave",
        "earned":  "earned_leave",
        "wfh":     "wfh_days",
    }[leave_type]

    conn = _conn()
    try:
        cur = conn.cursor()

        # Auto-provision if first-time employee
        _ensure_employee(cur, employee_id)

        # Read current balance
        cur.execute(
            f"SELECT {balance_col} AS balance FROM leave_balances WHERE employee_id = %s",
            (employee_id,),
        )
        row = cur.fetchone()
        if not row:
            return f"Could not read leave balance for '{employee_id}'."

        available = row["balance"]
        if days > available:
            return (
                f"Insufficient {leave_type} leave balance. "
                f"Requested: {days} days, available: {available} days. Request cannot be approved."
            )

        # Insert approved leave request
        cur.execute(
            """
            INSERT INTO leave_requests
                (employee_id, leave_type, start_date, end_date, days, reason, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'approved')
            RETURNING id
            """,
            (employee_id, leave_type, start_date, end_date, days, reason),
        )
        leave_id = cur.fetchone()["id"]

        # Deduct from balance
        cur.execute(
            f"""
            UPDATE leave_balances
               SET {balance_col} = {balance_col} - %s,
                   updated_at = NOW()
             WHERE employee_id = %s
            """,
            (days, employee_id),
        )

        conn.commit()

        return (
            f"Leave approved. Reference #{leave_id}.\n"
            f"  Employee    : {employee_id}\n"
            f"  Type        : {leave_type.title()} Leave\n"
            f"  Period      : {start_date} to {end_date} ({days} day{'s' if days > 1 else ''})\n"
            f"  Reason      : {reason or '—'}\n"
            f"  Balance left: {available - days} {leave_type} days remaining"
        )
    except Exception as e:
        conn.rollback()
        return f"Error approving leave: {e}"
    finally:
        conn.close()


@mcp.tool()
def get_leave_usage(employee_id: str) -> str:
    """Return the last 10 leave requests (all statuses) for an employee.

    Args:
        employee_id: Username portion of the employee email.
    """
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT lr.leave_type, lr.start_date::text, lr.end_date::text,
                   lr.days, lr.status, lr.reason
            FROM   leave_requests lr
            WHERE  lr.employee_id = %s
            ORDER  BY lr.start_date DESC
            LIMIT  10
            """,
            (employee_id,),
        )
        rows = cur.fetchall()
        if not rows:
            return f"No leave requests found for '{employee_id}'."

        lines = [f"Recent leave requests for {employee_id}:"]
        for r in rows:
            tag = r["status"].upper()
            lines.append(
                f"  [{tag:10s}] {r['leave_type'].title():8s} "
                f"{r['start_date']} → {r['end_date']} "
                f"({r['days']}d)  {r['reason']}"
            )
        return "\n".join(lines)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("MCP_PORT", "8001"))
    mcp.settings.host = "0.0.0.0"
    mcp.settings.port = port
    mcp.settings.stateless_http = True  # no session handshake — one POST per call
    # Disable DNS-rebinding protection so K8s pods can reach us via cluster DNS
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    )
    print(f"[HR Leave MCP Server] Starting on port {port} (streamable-http)", flush=True)
    mcp.run(transport="streamable-http")
