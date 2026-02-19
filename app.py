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
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from agents import build_graph
from rag import index_documents
import observability as obs
from observability import init_observability, get_metrics_app, get_tracer, get_langfuse_handler

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "")
if not SECRET_KEY:
    import warnings
    warnings.warn(
        "SECRET_KEY not set — using insecure default. Set SECRET_KEY env var in production.",
        stacklevel=1,
    )
    SECRET_KEY = "frontdeskai-dev-only-not-for-production"

AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "brainupgrade")
SQLITE_DIR = os.getenv("SQLITE_DIR", "/shared/.sqlite")
CHECKPOINT_DB = os.path.join(SQLITE_DIR, "checkpoints.db")
HISTORY_DB = os.path.join(SQLITE_DIR, "history.db")

os.makedirs(SQLITE_DIR, exist_ok=True)


def get_history_db():
    conn = sqlite3.connect(HISTORY_DB)
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
    conn.commit()
    return conn


# Initialize history table on import
get_history_db().close()


def create_token(email: str) -> str:
    payload = {
        "sub": email,
        "exp": datetime.now(timezone.utc) + timedelta(hours=24),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def verify_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        return payload["sub"]
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
    yield
    obs.logger.info("FrontDesk AI shutting down")


limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="FrontDesk AI", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, lambda req, exc: JSONResponse(
    status_code=429,
    content={"detail": "Too many requests. Please slow down."},
))
app.mount("/metrics", get_metrics_app())
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


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
@limiter.limit("5/minute")
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    if password != AUTH_PASSWORD:
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Invalid password"}
        )
    if not email or "@" not in email:
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Enter a valid email address"}
        )
    token = create_token(email)
    response = RedirectResponse(url="/chat", status_code=302)
    response.set_cookie(key="token", value=token, httponly=True, max_age=86400)
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
        "SELECT role, content, category, confidence, escalated, fallback_used, audit, created_at "
        "FROM messages WHERE email = ? ORDER BY id ASC",
        (user,),
    ).fetchall()
    conn.close()

    messages = []
    for row in rows:
        messages.append({
            "role": row[0],
            "content": row[1],
            "category": row[2],
            "confidence": row[3],
            "escalated": bool(row[4]),
            "fallback_used": bool(row[5]),
            "audit": row[6].split("||") if row[6] else [],
            "created_at": row[7],
        })

    return templates.TemplateResponse("chat.html", {
        "request": request,
        "user": user,
        "messages": messages,
    })


@app.post("/chat/send", response_class=JSONResponse)
@limiter.limit("10/minute")
async def send_message(request: Request, message: str = Form(...)):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

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

        # Extract employee name from email
        employee_name = user.split("@")[0].replace(".", " ").title()

        # Load recent conversation history for multi-turn context
        history_rows = conn.execute(
            "SELECT role, content FROM messages WHERE email = ? ORDER BY id DESC LIMIT 20",
            (user,),
        ).fetchall()
        # Reverse to chronological order (DB returns newest first)
        conversation_history = [
            {"role": row[0], "content": row[1]} for row in reversed(history_rows)
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
                        "request": message,
                        "conversation_history": conversation_history,
                        "category": "",
                        "confidence": 0,
                        "rag_context": "",
                        "rag_sources": [],
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

            result = await asyncio.to_thread(run_graph)

            category = result.get("category", "")
            confidence = result.get("confidence", 0)
            escalated = result.get("needs_escalation", False)
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
        conn.execute(
            "INSERT INTO messages (email, role, content, category, confidence, escalated, fallback_used, audit, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user, "assistant", final_response, category, confidence, int(escalated), int(fallback_used), audit_str, now),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "response": final_response,
        "category": category,
        "confidence": confidence,
        "escalated": escalated,
        "fallback_used": fallback_used,
        "audit": audit,
        "sources": result.get("rag_sources", []),
        "tool_calls": result.get("tool_calls_made", []),
    }


ADMIN_EMAIL = "admin@unigps.in"
POLICIES_DIR = os.path.join(os.path.dirname(__file__), "data", "policies")


@app.get("/kb", response_class=HTMLResponse)
async def kb_page(request: Request):
    """Knowledge base management page — admin only."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user != ADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="Admin access required (admin@unigps.in)")

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
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if user != ADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="Admin access required (admin@unigps.in)")

    if not file.filename or not file.filename.endswith(".md"):
        raise HTTPException(status_code=400, detail="Only .md (Markdown) files are accepted")

    content = await file.read()
    if len(content) > 1_000_000:  # 1MB limit
        raise HTTPException(status_code=400, detail="File too large (max 1MB)")

    os.makedirs(POLICIES_DIR, exist_ok=True)
    filepath = os.path.join(POLICIES_DIR, file.filename)
    with open(filepath, "wb") as f:
        f.write(content)

    # Re-index all documents
    chunk_count = index_documents(force=True)
    obs.logger.info(
        "Knowledge base updated",
        extra={"filename": file.filename, "chunks": chunk_count, "admin": user},
    )

    return {"status": "ok", "filename": file.filename, "total_chunks": chunk_count}


@app.post("/kb/delete", response_class=JSONResponse)
async def kb_delete(request: Request, filename: str = Form(...)):
    """Delete a policy document — admin only. Re-indexes the vector store."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if user != ADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="Admin access required (admin@unigps.in)")

    # Sanitize filename to prevent path traversal
    safe_name = os.path.basename(filename)
    filepath = os.path.join(POLICIES_DIR, safe_name)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Document not found")

    os.remove(filepath)
    chunk_count = index_documents(force=True)
    obs.logger.info(
        "Knowledge base document deleted",
        extra={"filename": safe_name, "chunks": chunk_count, "admin": user},
    )

    return {"status": "ok", "deleted": safe_name, "total_chunks": chunk_count}


@app.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request):
    """Analytics dashboard — admin only."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user != ADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="Admin access required (admin@unigps.in)")
    return templates.TemplateResponse("analytics.html", {"request": request, "user": user})


@app.get("/analytics/data", response_class=JSONResponse)
async def analytics_data(request: Request):
    """JSON API returning all analytics metrics — admin only."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if user != ADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="Admin access required")

    from tools import _get_db as get_tools_db

    data = {}

    # --- Conversation stats from history.db ---
    conn = get_history_db()
    try:
        row = conn.execute("SELECT COUNT(*) as total, COUNT(DISTINCT email) as users FROM messages").fetchone()
        data["total_messages"] = row[0]
        data["unique_users"] = row[1]

        row = conn.execute("SELECT COUNT(*) FROM messages WHERE date(created_at) = date('now')").fetchone()
        data["messages_today"] = row[0]

        row = conn.execute("SELECT COUNT(*) FROM messages WHERE date(created_at) >= date('now', '-7 days')").fetchone()
        data["messages_week"] = row[0]

        # Quality metrics from assistant messages
        qrow = conn.execute(
            "SELECT COUNT(*) as cnt, "
            "SUM(CASE WHEN escalated=1 THEN 1 ELSE 0 END) as escalated, "
            "SUM(CASE WHEN fallback_used=1 THEN 1 ELSE 0 END) as fallbacks, "
            "AVG(confidence) as avg_conf "
            "FROM messages WHERE role='assistant'"
        ).fetchone()
        responses = qrow[0] or 0
        data["escalation_rate"] = round((qrow[1] or 0) / responses * 100, 1) if responses else 0
        data["fallback_rate"] = round((qrow[2] or 0) / responses * 100, 1) if responses else 0
        data["avg_confidence"] = round(qrow[3] or 0, 1)

        # Category distribution
        cats = conn.execute(
            "SELECT category, COUNT(*) as cnt FROM messages "
            "WHERE role='assistant' AND category != '' GROUP BY category ORDER BY cnt DESC"
        ).fetchall()
        data["categories"] = [{"name": c[0], "count": c[1]} for c in cats]
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
            "SELECT ec.claim_id, e.full_name, ec.amount, ec.category, ec.status "
            "FROM expense_claims ec JOIN employees e ON ec.employee_id = e.employee_id "
            "WHERE ec.status IN ('submitted','under_review') ORDER BY ec.submitted_at"
        ).fetchall()
        data["pending_expenses"] = [
            {"claim_id": r["claim_id"], "employee": r["full_name"], "amount": r["amount"],
             "category": r["category"], "status": r["status"]}
            for r in expenses
        ]
        data["pending_expenses_total"] = sum(r["amount"] for r in expenses)

        # Pending leaves
        leaves = tconn.execute(
            "SELECT lr.leave_type, lr.start_date, lr.end_date, lr.days, e.full_name "
            "FROM leave_requests lr JOIN employees e ON lr.employee_id = e.employee_id "
            "WHERE lr.status = 'pending' ORDER BY lr.start_date"
        ).fetchall()
        data["pending_leaves"] = [
            {"employee": r["full_name"], "type": r["leave_type"], "start": r["start_date"],
             "end": r["end_date"], "days": r["days"]}
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
