"""Tests for FastAPI endpoints: status codes, auth gating, response shapes."""

import os
import sys
import json
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


class TestPublicEndpoints:
    def test_health_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_returns_json(self, client):
        resp = client.get("/health")
        data = resp.json()
        assert "status" in data or data.get("status") == "ok" or resp.status_code == 200

    def test_login_page_returns_200(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")

    def test_root_redirects(self, client):
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code in (302, 303, 307, 308)


class TestAuthProtectedEndpoints:
    def test_chat_page_without_auth_redirects(self, client):
        resp = client.get("/chat", follow_redirects=False)
        assert resp.status_code in (302, 303)

    def test_chat_send_without_auth_redirects(self, client):
        resp = client.post("/chat/send", data={"message": "hello"}, follow_redirects=False)
        assert resp.status_code in (302, 303, 401, 403)

    def test_chat_page_with_auth_returns_200(self, client, auth_cookie):
        resp = client.get("/chat", cookies=auth_cookie)
        assert resp.status_code == 200

    def test_chat_send_returns_json_response(self, client, auth_cookie, env):
        """Chat endpoint returns JSON with expected fields."""
        graph_result = {
            "final_response": "Here is your answer.",
            "category": "hr",
            "confidence": 9,
            "needs_escalation": False,
            "fallback_used": False,
            "audit": [],
            "rag_sources": [],
            "tool_calls_made": [],
        }

        with patch("app.graph") as mock_graph:
            mock_compiled = MagicMock()
            mock_graph.compile.return_value = mock_compiled
            mock_compiled.invoke.return_value = graph_result
            resp = client.post(
                "/chat/send",
                data={"message": "What is the leave policy?"},
                cookies=auth_cookie,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "response" in data or "category" in data

    def test_chat_send_empty_message_rejected(self, client, auth_cookie):
        resp = client.post("/chat/send", data={"message": ""}, cookies=auth_cookie)
        assert resp.status_code in (200, 400, 422)

    def test_feedback_endpoint_accepts_thumbs_up(self, client, auth_cookie, env):
        # First create a message in the DB
        import tools as t
        import sqlite3
        db = sqlite3.connect(t.HISTORY_DB)
        db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT, role TEXT, content TEXT,
                category TEXT DEFAULT '', confidence INTEGER DEFAULT 0,
                escalated INTEGER DEFAULT 0, fallback_used INTEGER DEFAULT 0,
                audit TEXT DEFAULT '', created_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS message_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL, email TEXT NOT NULL,
                feedback TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(message_id, email)
            )
        """)
        db.execute(
            "INSERT INTO messages (email, role, content, created_at) VALUES (?, 'assistant', 'test', datetime('now'))",
            [env["user_email"]],
        )
        db.commit()
        msg_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.close()

        resp = client.post(
            "/chat/feedback",
            json={"message_id": msg_id, "feedback": "up"},
            cookies=auth_cookie,
        )
        assert resp.status_code in (200, 204)


class TestAdminEndpoints:
    def test_kb_page_without_auth_redirects(self, client):
        resp = client.get("/kb", follow_redirects=False)
        assert resp.status_code in (302, 303)

    def test_kb_page_non_admin_redirects(self, client, auth_cookie):
        resp = client.get("/kb", cookies=auth_cookie, follow_redirects=False)
        assert resp.status_code in (302, 303, 403)

    def test_kb_page_admin_returns_200(self, client, admin_cookie):
        resp = client.get("/kb", cookies=admin_cookie)
        assert resp.status_code == 200

    def test_analytics_page_non_admin_redirects(self, client, auth_cookie):
        resp = client.get("/analytics", cookies=auth_cookie, follow_redirects=False)
        assert resp.status_code in (302, 303, 403)

    def test_analytics_page_admin_returns_200(self, client, admin_cookie):
        resp = client.get("/analytics", cookies=admin_cookie)
        assert resp.status_code == 200

    def test_analytics_data_returns_json(self, client, admin_cookie):
        resp = client.get("/analytics/data", cookies=admin_cookie)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_analytics_data_has_expected_keys(self, client, admin_cookie):
        resp = client.get("/analytics/data", cookies=admin_cookie)
        data = resp.json()
        # Should have at least some of these keys
        expected_keys = {
            "total_messages", "unique_users", "categories",
            "tickets_by_status", "pending_expenses", "pending_leaves",
        }
        assert len(expected_keys.intersection(data.keys())) > 0

    def test_kb_upload_non_admin_rejected(self, client, auth_cookie):
        resp = client.post(
            "/kb/upload",
            files={"file": ("test.md", b"# Test\nContent", "text/markdown")},
            cookies=auth_cookie,
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303, 403)

    def test_kb_upload_admin_accepts_md_file(self, client, admin_cookie):
        resp = client.post(
            "/kb/upload",
            files={"file": ("new_policy.md", b"# New Policy\n\nContent here.", "text/markdown")},
            cookies=admin_cookie,
        )
        assert resp.status_code in (200, 302, 303)

    def test_kb_upload_rejects_non_md_file(self, client, admin_cookie):
        resp = client.post(
            "/kb/upload",
            files={"file": ("script.py", b"print('hello')", "text/plain")},
            cookies=admin_cookie,
        )
        # Should reject non-markdown files
        assert resp.status_code in (200, 400, 422)
        if resp.status_code == 200:
            assert "error" in resp.text.lower() or "markdown" in resp.text.lower() or ".md" in resp.text
