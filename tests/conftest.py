"""Shared fixtures for FrontDesk AI test suite.

All tests use isolated temporary SQLite databases — no shared state between tests.
LLM calls are mocked; no Groq/OpenRouter API keys required to run the suite.
"""

import os
import sqlite3
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Add app/ to path so imports work from tests/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


# ---------------------------------------------------------------------------
# Environment + DB isolation
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def tmp_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("frontdeskai")


@pytest.fixture()
def sqlite_dir(tmp_path):
    """Fresh temporary SQLITE_DIR for each test."""
    d = tmp_path / "sqlite"
    d.mkdir()
    return str(d)


@pytest.fixture()
def chroma_dir(tmp_path):
    """Fresh temporary ChromaDB dir for each test."""
    d = tmp_path / "chromadb"
    d.mkdir()
    return str(d)


@pytest.fixture()
def env(sqlite_dir, chroma_dir, monkeypatch):
    """Patch all environment variables AND module-level DB path constants."""
    monkeypatch.setenv("SQLITE_DIR", sqlite_dir)
    monkeypatch.setenv("CHROMA_DIR", chroma_dir)
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-for-jwt-signing-32chars!")
    monkeypatch.setenv("AUTH_PASSWORD", "testpass")
    monkeypatch.setenv("ADMIN_EMAILS", "admin@test.com")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_key")
    monkeypatch.setenv("SEED_DEMO_DATA", "false")

    # Patch module-level DB path constants (computed at import time, not runtime)
    import tools
    import auth
    import skills
    tools_db = os.path.join(sqlite_dir, "frontdesk_tools.db")
    history_db = os.path.join(sqlite_dir, "history.db")
    skills_dir = os.path.join(sqlite_dir, "skills")
    os.makedirs(skills_dir, exist_ok=True)
    monkeypatch.setattr(tools, "TOOLS_DB", tools_db)
    monkeypatch.setattr(tools, "HISTORY_DB", history_db)
    # auth.py imports HISTORY_DB from tools at import time — patch auth's reference too
    monkeypatch.setattr(auth, "HISTORY_DB", history_db, raising=False)
    # skills.py derives SKILLS_DIR from SQLITE_DIR at import time — patch it too
    monkeypatch.setattr(skills, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(skills, "_loaded_skills", {})
    # Reset the one-time init flag and immediately initialize schema in the fresh test DB
    tools._db_initialized = False
    tools._get_db().close()   # creates schema + seeds system_config in the test DB

    return {
        "sqlite_dir": sqlite_dir,
        "chroma_dir": chroma_dir,
        "admin_email": "admin@test.com",
        "user_email": "user@test.com",
        "password": "testpass",
    }


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _tools_db_path(sqlite_dir):
    return os.path.join(sqlite_dir, "frontdesk_tools.db")

def _history_db_path(sqlite_dir):
    return os.path.join(sqlite_dir, "history.db")


@pytest.fixture()
def tools_db(sqlite_dir):
    """Initialize and return a tools DB connection for the test."""
    from tools import _get_db
    _get_db().close()
    conn = sqlite3.connect(_tools_db_path(sqlite_dir))
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture()
def seeded_tools_db(tools_db, sqlite_dir):
    """Tools DB pre-seeded with a test employee and related records."""
    db = tools_db
    db.execute("""
        INSERT OR IGNORE INTO employees
            (employee_id, full_name, email, department, designation, date_of_join, is_active)
        VALUES ('EMP001', 'Test User', 'user@test.com', 'Engineering', 'Engineer', '2023-01-01', 1)
    """)
    db.execute("""
        INSERT OR IGNORE INTO leave_balances
            (employee_id, casual_leave, sick_leave, earned_leave, wfh_days, year)
        VALUES ('EMP001', 10, 8, 15, 20, 2026)
    """)
    db.execute("""
        INSERT OR IGNORE INTO payslips
            (employee_id, month, gross_salary, deductions, net_salary, status, generated_at)
        VALUES ('EMP001', '2026-03', 100000, 20000, 80000, 'generated', '2026-03-31')
    """)
    db.commit()
    yield db


# ---------------------------------------------------------------------------
# FastAPI TestClient
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(env, tmp_path, monkeypatch):
    """TestClient with mocked LLM, isolated DBs, and disabled ChromaDB startup."""
    policies_dir = str(tmp_path / "policies")
    os.makedirs(policies_dir, exist_ok=True)

    # Write a minimal policy file so RAG indexing doesn't error
    with open(os.path.join(policies_dir, "hr-handbook.md"), "w") as f:
        f.write("# HR Handbook\n## Leave Policy\nEmployees get 10 casual leaves per year.\n")

    mock_llm = MagicMock()
    mock_graph = MagicMock()

    history_db = env["sqlite_dir"] and os.path.join(env["sqlite_dir"], "history.db")

    with (
        patch("agents.ChatGroq", return_value=mock_llm),
        patch("agents.build_graph", return_value=mock_graph),
        patch("rag.POLICIES_DIR", policies_dir),
        patch("skills.load_all_skills", return_value=0),
        patch("observability.init_observability"),
        patch("observability.get_langfuse_handler", return_value=None),
    ):
        from app import app
        import app as app_module
        # app.py imports HISTORY_DB from tools at module level — patch the local copy too
        monkeypatch.setattr(app_module, "HISTORY_DB", history_db, raising=False)
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c


@pytest.fixture()
def auth_cookie(client, env):
    """Return cookies dict with valid JWT for a regular user."""
    resp = client.post(
        "/login",
        data={"email": env["user_email"], "password": env["password"]},
        follow_redirects=False,
    )
    assert resp.status_code in (200, 302, 303)
    return {"token": resp.cookies.get("token")}


@pytest.fixture()
def admin_cookie(client, env):
    """Return cookies dict with valid JWT for the admin user."""
    resp = client.post(
        "/login",
        data={"email": env["admin_email"], "password": env["password"]},
        follow_redirects=False,
    )
    assert resp.status_code in (200, 302, 303)
    return {"token": resp.cookies.get("token")}
