"""FrontDesk AI — Agentic AI Support System with Web UI."""

import os
import sqlite3
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

import jwt
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from langgraph.checkpoint.sqlite import SqliteSaver

from agents import build_graph, SupportRequest

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "frontdeskai-default-secret-change-me")
AUTH_PASSWORD = "brainupgrade"
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
        "exp": datetime.utcnow() + timedelta(hours=24),
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
    yield


app = FastAPI(title="FrontDesk AI", lifespan=lifespan)
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
async def send_message(request: Request, message: str = Form(...)):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Save user message
    conn = get_history_db()
    conn.execute(
        "INSERT INTO messages (email, role, content, created_at) VALUES (?, ?, ?, ?)",
        (user, "user", message, now),
    )
    conn.commit()

    # Extract employee name from email
    employee_name = user.split("@")[0].replace(".", " ").title()

    # Run the multi-agent graph with SQLite checkpointer
    with SqliteSaver.from_conn_string(CHECKPOINT_DB) as checkpointer:
        compiled = graph.compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": user}}

        initial_state = {
            "employee_name": employee_name,
            "request": message,
            "category": "",
            "confidence": 0,
            "worker_output": "",
            "needs_escalation": False,
            "escalation_reason": "",
            "error": "",
            "fallback_used": False,
            "final_response": "",
            "audit": [],
        }

        result = compiled.invoke(initial_state, config)

    category = result.get("category", "")
    confidence = result.get("confidence", 0)
    escalated = result.get("needs_escalation", False)
    fallback_used = result.get("fallback_used", False)
    final_response = result.get("final_response", "Something went wrong.")
    audit = result.get("audit", [])
    audit_str = "||".join(audit)

    # Save assistant response
    conn.execute(
        "INSERT INTO messages (email, role, content, category, confidence, escalated, fallback_used, audit, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user, "assistant", final_response, category, confidence, int(escalated), int(fallback_used), audit_str, now),
    )
    conn.commit()
    conn.close()

    return {
        "response": final_response,
        "category": category,
        "confidence": confidence,
        "escalated": escalated,
        "fallback_used": fallback_used,
        "audit": audit,
    }


@app.get("/health")
async def health():
    return {"status": "ok", "service": "frontdeskai"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
