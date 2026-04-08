"""FrontDesk AI — Agentic AI Support System with Web UI."""

import asyncio
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager

import jwt
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from langgraph.checkpoint.sqlite import SqliteSaver

from agents import build_graph
from auth import get_user_password, set_user_password, verify_password, current_user_email, _get_auth_db
from rag import index_documents, POLICIES_DIR
from skills import load_all_skills
from tools import HISTORY_DB
import observability as obs
from observability import init_observability, get_metrics_app, get_tracer, get_langfuse_handler

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "")
if not SECRET_KEY:
    if os.getenv("ENV") == "production":
        raise RuntimeError("SECRET_KEY must be set in production. Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\"")
    import secrets as _secrets
    SECRET_KEY = _secrets.token_urlsafe(32)
    import warnings
    warnings.warn(
        "SECRET_KEY not set — generated random key for this session. Tokens will not survive restart.",
        stacklevel=1,
    )

AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "brainupgrade")
SQLITE_DIR = os.getenv("SQLITE_DIR", "/shared/.sqlite")
CHECKPOINT_DB = os.path.join(SQLITE_DIR, "checkpoints.db")

os.makedirs(SQLITE_DIR, exist_ok=True)


def get_history_db():
    conn = sqlite3.connect(HISTORY_DB)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            category TEXT DEFAULT '',
            confidence INTEGER DEFAULT 0,
            escalated INTEGER DEFAULT 0,
            fallback_used INTEGER DEFAULT 0,
            audit TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS message_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL,
            email TEXT NOT NULL,
            feedback TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(message_id, email)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS fewshot_examples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL UNIQUE,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            category TEXT NOT NULL,
            confidence INTEGER NOT NULL,
            email TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    conn.commit()
    return conn


# Initialize history table on import
get_history_db().close()
# Initialize auth (users) table on import
_get_auth_db().close()


def create_token(email: str) -> str:
    payload = {
        "sub": email,
        "exp": datetime.now(timezone.utc) + timedelta(hours=24),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def verify_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        email = payload.get("sub")
        if not email or not isinstance(email, str) or "@" not in email:
            return None
        return email
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


def get_current_user(request: Request) -> str | None:
    token = request.cookies.get("token")
    if not token:
        return None
    return verify_token(token)


# Build graph once at startup
graph = build_graph()


@asynccontextmanager
async def lifespan(application: FastAPI):
    init_observability()
    obs.logger.info("FrontDesk AI starting up")
    # Index policy documents into vector store on startup
    chunk_count = index_documents()
    obs.logger.info("RAG index ready", extra={"chunks": chunk_count})
    skill_count = load_all_skills()
    obs.logger.info("Skills loaded", extra={"skills": skill_count})
    yield
    obs.logger.info("FrontDesk AI shutting down")


app = FastAPI(title="FrontDesk AI", lifespan=lifespan)
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; frame-ancestors 'none'"
    )
    return response

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/metrics", get_metrics_app())
app.mount("/static", StaticFiles(directory=os.path.join(_APP_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(_APP_DIR, "templates"))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return RedirectResponse(url="/chat", status_code=302)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": ""})


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    if not email or "@" not in email:
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Invalid email or password"}
        )

    # Check for per-user stored password first
    user_record = get_user_password(email)
    if user_record:
        # User has a stored password — verify against it
        if not verify_password(password, user_record["password_hash"], user_record["password_salt"]):
            return templates.TemplateResponse(
                "login.html", {"request": request, "error": "Invalid email or password"}
            )
    else:
        # No stored password — verify against shared AUTH_PASSWORD, then save
        if password != AUTH_PASSWORD:
            return templates.TemplateResponse(
                "login.html", {"request": request, "error": "Invalid email or password"}
            )
        set_user_password(email, password)

    token = create_token(email)
    response = RedirectResponse(url="/chat", status_code=302)
    response.set_cookie(key="token", value=token, httponly=True, samesite="strict", max_age=86400)
    return response


@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("token")
    return response


@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    conn = get_history_db()
    rows = conn.execute(
        "SELECT m.id, m.role, m.content, m.category, m.confidence, m.escalated, m.fallback_used, m.audit, m.created_at, "
        "mf.feedback "
        "FROM messages m LEFT JOIN message_feedback mf ON m.id = mf.message_id AND mf.email = ? "
        "WHERE m.email = ? ORDER BY m.id ASC",
        (user, user),
    ).fetchall()
    conn.close()

    messages = []
    for row in rows:
        messages.append({
            "id": row["id"],
            "role": row["role"],
            "content": row["content"],
            "category": row["category"],
            "confidence": row["confidence"],
            "escalated": bool(row["escalated"]),
            "fallback_used": bool(row["fallback_used"]),
            "audit": row["audit"].split("||") if row["audit"] else [],
            "created_at": row["created_at"],
            "feedback": row["feedback"],
        })

    return templates.TemplateResponse("chat.html", {
        "request": request,
        "user": user,
        "is_admin": user in ADMIN_EMAILS,
        "messages": messages,
    })


@app.post("/chat/send", response_class=JSONResponse)
async def send_message(request: Request, message: str = Form(...)):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Input validation — reject empty or excessively long messages
    message = message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    if len(message) > 5000:
        raise HTTPException(status_code=400, detail="Message too long (max 5000 characters)")

    start = time.monotonic()
    tracer = get_tracer()

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Save user message
    conn = get_history_db()
    try:
        conn.execute(
            "INSERT INTO messages (email, role, content, created_at) VALUES (?, ?, ?, ?)",
            (user, "user", message, now),
        )
        conn.commit()

        # Extract employee name and ID from email
        employee_id = user.split("@")[0]
        employee_name = employee_id.replace(".", " ").title()

        # Load recent conversation history for multi-turn context
        history_rows = conn.execute(
            "SELECT role, content FROM messages WHERE email = ? ORDER BY id DESC LIMIT 20",
            (user,),
        ).fetchall()
        # Reverse to chronological order (DB returns newest first)
        conversation_history = [
            {"role": row["role"], "content": row["content"]} for row in reversed(history_rows)
        ]

        with tracer.start_as_current_span("chat.send") as span:
            span.set_attribute("user.email", user)
            span.set_attribute("chat.history_turns", len(conversation_history))

            # Run the multi-agent graph in a thread to avoid blocking the event loop
            def run_graph():
                with SqliteSaver.from_conn_string(CHECKPOINT_DB) as checkpointer:
                    compiled = graph.compile(checkpointer=checkpointer)
                    config = {"configurable": {"thread_id": user}}

                    # Attach Langfuse callback if configured
                    lf_handler = get_langfuse_handler(user_id=user, session_id=user)
                    if lf_handler:
                        config["callbacks"] = [lf_handler]

                    initial_state = {
                        "employee_name": employee_name,
                        "employee_id": employee_id,
                        "request": message,
                        "conversation_history": conversation_history,
                        "is_admin": user in ADMIN_EMAILS,
                        "category": "",
                        "confidence": 0,
                        "rag_context": "",
                        "rag_sources": [],
                        "fewshot_context": "",
                        "worker_output": "",
                        "needs_escalation": False,
                        "escalation_reason": "",
                        "tool_calls_made": [],
                        "react_iterations": 0,
                        "qa_retry_count": 0,
                        "qa_feedback": "",
                        "error": "",
                        "fallback_used": False,
                        "final_response": "",
                        "audit": [],
                    }

                    return compiled.invoke(initial_state, config)

            # Set the authenticated user email for tools (e.g. change_my_password)
            current_user_email.set(user)

            result = await asyncio.to_thread(run_graph)

            _VALID_CATEGORIES = {"hr", "tech", "finance", "facilities", "analytics", "account", "skill_admin", "general"}
            category = result.get("category", "")
            if category not in _VALID_CATEGORIES:
                category = "general"
            confidence = max(0, min(10, int(result.get("confidence", 0))))
            escalated = bool(result.get("needs_escalation", False))
            fallback_used = result.get("fallback_used", False)
            final_response = result.get("final_response", "Something went wrong.")
            audit = result.get("audit", [])
            audit_str = "||".join(audit)

            span.set_attribute("chat.category", category)
            span.set_attribute("chat.confidence", confidence)
            span.set_attribute("chat.escalated", escalated)

            # Record metrics
            if obs.category_counter:
                obs.category_counter.add(1, {"category": category})
            if escalated and obs.escalation_counter:
                obs.escalation_counter.add(1, {"category": category})
            if fallback_used and obs.fallback_counter:
                obs.fallback_counter.add(1, {"category": category})

            elapsed = time.monotonic() - start
            if obs.request_duration:
                obs.request_duration.record(elapsed)

            obs.logger.info(
                "Chat request processed",
                extra={"category": category, "employee": user, "duration_ms": round(elapsed * 1000, 1)},
            )

        # Save assistant response
        cursor = conn.execute(
            "INSERT INTO messages (email, role, content, category, confidence, escalated, fallback_used, audit, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user, "assistant", final_response, category, confidence, int(escalated), int(fallback_used), audit_str, now),
        )
        message_id = cursor.lastrowid
        conn.commit()
    finally:
        conn.close()

    response_data: dict = {
        "response": final_response,
        "category": category,
        "confidence": confidence,
        "escalated": escalated,
        "fallback_used": fallback_used,
        "message_id": message_id,
        "sources": result.get("rag_sources", []),
    }
    # Only expose internal details (audit trail, tool calls) to admins
    if user in ADMIN_EMAILS:
        response_data["audit"] = audit
        response_data["tool_calls"] = result.get("tool_calls_made", [])

    return response_data


def _qualifies_for_fewshot(msg_row) -> bool:
    """Check if a message qualifies to become a few-shot example."""
    confidence = msg_row["confidence"] or 0
    escalated = bool(msg_row["escalated"])
    fallback_used = bool(msg_row["fallback_used"])
    category = msg_row["category"] or ""
    return (
        confidence >= 7
        and not escalated
        and not fallback_used
        and category not in ("", "general")
    )


def _add_fewshot_example(conn, msg_row, user: str) -> None:
    """Extract preceding user question and store the Q&A pair as a few-shot example."""
    from fewshot import add_example as fewshot_add

    message_id = msg_row["id"]
    answer = msg_row["content"]
    category = msg_row["category"]
    confidence = msg_row["confidence"]

    # Find the preceding user message
    user_msg = conn.execute(
        "SELECT content FROM messages WHERE email = ? AND role = 'user' AND id < ? ORDER BY id DESC LIMIT 1",
        (user, message_id),
    ).fetchone()
    if not user_msg:
        return

    question = user_msg["content"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    example_id = f"fewshot_{message_id}"

    # Insert into SQLite
    conn.execute(
        "INSERT OR REPLACE INTO fewshot_examples (message_id, question, answer, category, confidence, email, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (message_id, question, answer, category, confidence, user, now),
    )
    conn.commit()

    # Insert into ChromaDB
    fewshot_add(example_id, question, answer, category, confidence)


def _remove_fewshot_example(conn, message_id: int) -> None:
    """Remove a few-shot example from both SQLite and ChromaDB."""
    from fewshot import remove_example as fewshot_remove

    conn.execute("DELETE FROM fewshot_examples WHERE message_id = ?", (message_id,))
    conn.commit()
    fewshot_remove(f"fewshot_{message_id}")


@app.post("/chat/feedback", response_class=JSONResponse)
async def chat_feedback(request: Request):
    """Submit thumbs up/down feedback on an assistant message."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    body = await request.json()
    message_id = body.get("message_id")
    feedback = body.get("feedback")

    if feedback not in ("up", "down"):
        raise HTTPException(status_code=400, detail="feedback must be 'up' or 'down'")
    if not isinstance(message_id, int):
        raise HTTPException(status_code=400, detail="message_id must be an integer")

    conn = get_history_db()
    try:
        # Validate: message exists, belongs to user, is assistant role
        msg = conn.execute(
            "SELECT id, role, email, content, category, confidence, escalated, fallback_used FROM messages WHERE id = ?",
            (message_id,),
        ).fetchone()
        if not msg:
            raise HTTPException(status_code=404, detail="Message not found")
        if msg["email"] != user:
            raise HTTPException(status_code=403, detail="Not your message")
        if msg["role"] != "assistant":
            raise HTTPException(status_code=400, detail="Can only rate assistant messages")

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Upsert feedback
        conn.execute(
            "INSERT INTO message_feedback (message_id, email, feedback, created_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(message_id, email) DO UPDATE SET feedback = excluded.feedback, created_at = excluded.created_at",
            (message_id, user, feedback, now),
        )
        conn.commit()

        # Few-shot memory management
        if feedback == "up" and _qualifies_for_fewshot(msg):
            _add_fewshot_example(conn, msg, user)
        elif feedback == "down":
            _remove_fewshot_example(conn, message_id)
    finally:
        conn.close()

    return {"status": "ok"}


ADMIN_EMAILS = set(
    e.strip() for e in os.getenv("ADMIN_EMAILS", "admin@unigps.in").split(",") if e.strip()
)


def _require_admin(user: str | None) -> str:
    """Validate user is authenticated and is an admin. Returns user email or raises."""
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if user not in ADMIN_EMAILS:
        raise HTTPException(status_code=403, detail="Access denied")
    return user


@app.get("/kb", response_class=HTMLResponse)
async def kb_page(request: Request):
    """Knowledge base management page — admin only."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    _require_admin(user)

    # List current policy documents
    import glob
    docs = []
    for filepath in sorted(glob.glob(os.path.join(POLICIES_DIR, "*.md"))):
        filename = os.path.basename(filepath)
        size = os.path.getsize(filepath)
        docs.append({"filename": filename, "size_kb": round(size / 1024, 1)})

    return templates.TemplateResponse("kb.html", {
        "request": request,
        "user": user,
        "docs": docs,
    })


@app.post("/kb/upload", response_class=JSONResponse)
async def kb_upload(request: Request, file: UploadFile = File(...)):
    """Upload a new policy document — admin only. Re-indexes the vector store."""
    user = _require_admin(get_current_user(request))

    if not file.filename or not file.filename.endswith(".md"):
        raise HTTPException(status_code=400, detail="Only .md (Markdown) files are accepted")

    content = await file.read()
    if len(content) > 1_000_000:  # 1MB limit
        raise HTTPException(status_code=400, detail="File too large (max 1MB)")

    # Validate content is valid UTF-8 text (not a binary disguised as .md)
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File is not valid UTF-8 text")

    # Sanitize filename — prevent path traversal
    safe_filename = os.path.basename(file.filename)
    os.makedirs(POLICIES_DIR, exist_ok=True)
    filepath = os.path.abspath(os.path.join(POLICIES_DIR, safe_filename))
    if not filepath.startswith(os.path.abspath(POLICIES_DIR)):
        raise HTTPException(status_code=400, detail="Invalid filename")

    with open(filepath, "wb") as f:
        f.write(content)

    # Re-index all documents
    chunk_count = index_documents(force=True)
    obs.logger.info(
        "Knowledge base updated",
        extra={"doc_filename": safe_filename, "chunks": chunk_count, "admin": user},
    )

    return {"status": "ok", "filename": safe_filename, "total_chunks": chunk_count}


@app.post("/kb/delete", response_class=JSONResponse)
async def kb_delete(request: Request, filename: str = Form(...)):
    """Delete a policy document — admin only. Re-indexes the vector store."""
    user = _require_admin(get_current_user(request))

    if not filename.endswith(".md"):
        raise HTTPException(status_code=400, detail="Only .md files can be deleted")

    # Sanitize filename to prevent path traversal
    safe_name = os.path.basename(filename)
    filepath = os.path.abspath(os.path.join(POLICIES_DIR, safe_name))
    if not filepath.startswith(os.path.abspath(POLICIES_DIR)):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Document not found")

    os.remove(filepath)
    chunk_count = index_documents(force=True)
    obs.logger.info(
        "Knowledge base document deleted",
        extra={"doc_filename": safe_name, "chunks": chunk_count, "admin": user},
    )

    return {"status": "ok", "deleted": safe_name, "total_chunks": chunk_count}


@app.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request):
    """Analytics dashboard — admin only."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    _require_admin(user)
    return templates.TemplateResponse("analytics.html", {"request": request, "user": user})


@app.get("/analytics/data", response_class=JSONResponse)
async def analytics_data(request: Request):
    """JSON API returning all analytics metrics — admin only."""
    user = _require_admin(get_current_user(request))

    from tools import _get_db as get_tools_db

    data = {}

    # --- Conversation stats from history.db ---
    conn = get_history_db()
    try:
        row = conn.execute("SELECT COUNT(*) as total, COUNT(DISTINCT email) as users FROM messages").fetchone()
        data["total_messages"] = row["total"]
        data["unique_users"] = row["users"]

        row = conn.execute("SELECT COUNT(*) as cnt FROM messages WHERE date(created_at) = date('now')").fetchone()
        data["messages_today"] = row["cnt"]

        row = conn.execute("SELECT COUNT(*) as cnt FROM messages WHERE date(created_at) >= date('now', '-7 days')").fetchone()
        data["messages_week"] = row["cnt"]

        # Quality metrics from assistant messages
        qrow = conn.execute(
            "SELECT COUNT(*) as cnt, "
            "SUM(CASE WHEN escalated=1 THEN 1 ELSE 0 END) as escalated, "
            "SUM(CASE WHEN fallback_used=1 THEN 1 ELSE 0 END) as fallbacks, "
            "AVG(confidence) as avg_conf "
            "FROM messages WHERE role='assistant'"
        ).fetchone()
        responses = qrow["cnt"] or 0
        data["escalation_rate"] = round((qrow["escalated"] or 0) / responses * 100, 1) if responses else 0
        data["fallback_rate"] = round((qrow["fallbacks"] or 0) / responses * 100, 1) if responses else 0
        data["avg_confidence"] = round(qrow["avg_conf"] or 0, 1)

        # Category distribution
        cats = conn.execute(
            "SELECT category, COUNT(*) as cnt FROM messages "
            "WHERE role='assistant' AND COALESCE(category, '') != '' GROUP BY category ORDER BY cnt DESC"
        ).fetchall()
        data["categories"] = [{"name": c["category"], "count": c["cnt"]} for c in cats]
    finally:
        conn.close()

    # --- Tickets from tools db ---
    tconn = get_tools_db()
    try:
        by_status = tconn.execute("SELECT status, COUNT(*) as cnt FROM tickets GROUP BY status").fetchall()
        data["tickets_by_status"] = [{"status": r["status"], "count": r["cnt"]} for r in by_status]

        by_priority = tconn.execute(
            "SELECT priority, COUNT(*) as cnt FROM tickets "
            "WHERE status NOT IN ('Closed','Resolved') GROUP BY priority ORDER BY priority"
        ).fetchall()
        data["tickets_by_priority"] = [{"priority": r["priority"], "count": r["cnt"]} for r in by_priority]

        # Pending expenses
        expenses = tconn.execute(
            "SELECT ec.claim_id, e.full_name, ec.amount, ec.category, ec.status, ec.submitted_at "
            "FROM expense_claims ec JOIN employees e ON ec.employee_id = e.employee_id "
            "WHERE ec.status IN ('submitted','under_review') ORDER BY ec.submitted_at"
        ).fetchall()
        data["pending_expenses"] = [
            {"claim_id": r["claim_id"], "employee": r["full_name"], "amount": r["amount"],
             "category": r["category"], "status": r["status"], "submitted": r["submitted_at"]}
            for r in expenses
        ]
        data["pending_expenses_total"] = sum(r["amount"] for r in expenses)

        # Pending leaves
        leaves = tconn.execute(
            "SELECT lr.leave_type, lr.start_date, lr.end_date, lr.days, lr.reason, e.full_name "
            "FROM leave_requests lr JOIN employees e ON lr.employee_id = e.employee_id "
            "WHERE lr.status = 'pending' ORDER BY lr.start_date"
        ).fetchall()
        data["pending_leaves"] = [
            {"employee": r["full_name"], "type": r["leave_type"], "start": r["start_date"],
             "end": r["end_date"], "days": r["days"], "reason": r["reason"]}
            for r in leaves
        ]

        # Room utilization (last 7 days)
        rooms = tconn.execute(
            "SELECT mr.room_name, COUNT(rb.id) as bookings "
            "FROM meeting_rooms mr "
            "LEFT JOIN room_bookings rb ON mr.room_id = rb.room_id "
            "  AND rb.status = 'confirmed' "
            "  AND rb.date >= date('now', '-7 days') AND rb.date <= date('now', '+1 day') "
            "WHERE mr.is_active = 1 GROUP BY mr.room_id ORDER BY bookings DESC"
        ).fetchall()
        data["room_utilization"] = [{"room": r["room_name"], "bookings": r["bookings"]} for r in rooms]
    finally:
        tconn.close()

    return data


@app.get("/health")
async def health():
    return {"status": "ok", "service": "frontdeskai"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
