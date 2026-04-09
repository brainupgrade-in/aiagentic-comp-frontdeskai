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
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def get_leave_balance(employee_id: str) -> str:
    """Return remaining leave balance for an employee.

    Args:
        employee_id: Username portion of the employee email
                     (e.g. 'alice' for alice@unigps.in).
    """
    conn = _conn()
    try:
        cur = conn.cursor()

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
            return (
                f"No leave record found for '{employee_id}'. "
                "Please contact HR to set up the account."
            )

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

        lines = [
            f"Leave balance for {emp['full_name']} ({emp['department']}) — {emp['year']}:",
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
