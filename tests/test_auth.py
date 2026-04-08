"""Tests for authentication: login, JWT, per-user password hashing, admin gating."""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


class TestPasswordHashing:
    def test_hash_returns_two_values(self, env):
        from auth import hash_password
        h, s = hash_password("mypassword")
        assert isinstance(h, str) and len(h) == 64   # 32-byte hex
        assert isinstance(s, str) and len(s) == 64

    def test_hash_is_deterministic_with_same_salt(self, env):
        from auth import hash_password
        h1, s1 = hash_password("mypassword")
        h2, _ = hash_password("mypassword", salt=bytes.fromhex(s1))
        assert h1 == h2

    def test_different_passwords_produce_different_hashes(self, env):
        from auth import hash_password
        h1, s = hash_password("password1")
        h2, _ = hash_password("password2", salt=bytes.fromhex(s))
        assert h1 != h2

    def test_verify_correct_password(self, env):
        from auth import hash_password, verify_password
        h, s = hash_password("correct")
        assert verify_password("correct", h, s) is True

    def test_verify_wrong_password(self, env):
        from auth import hash_password, verify_password
        h, s = hash_password("correct")
        assert verify_password("wrong", h, s) is False


class TestUserStorage:
    def test_set_and_get_user_password(self, env):
        from auth import set_user_password, get_user_password, verify_password
        set_user_password("alice@test.com", "secret123")
        row = get_user_password("alice@test.com")
        assert row is not None
        assert verify_password("secret123", row["password_hash"], row["password_salt"])

    def test_get_nonexistent_user_returns_none(self, env):
        from auth import get_user_password
        assert get_user_password("nobody@test.com") is None

    def test_password_update_overwrites_old(self, env):
        from auth import set_user_password, get_user_password, verify_password
        set_user_password("bob@test.com", "first")
        set_user_password("bob@test.com", "second")
        row = get_user_password("bob@test.com")
        assert verify_password("second", row["password_hash"], row["password_salt"])
        assert not verify_password("first", row["password_hash"], row["password_salt"])

    def test_different_users_have_independent_passwords(self, env):
        from auth import set_user_password, get_user_password, verify_password
        set_user_password("user1@test.com", "pass1")
        set_user_password("user2@test.com", "pass2")
        r1 = get_user_password("user1@test.com")
        r2 = get_user_password("user2@test.com")
        assert verify_password("pass1", r1["password_hash"], r1["password_salt"])
        assert verify_password("pass2", r2["password_hash"], r2["password_salt"])
        assert not verify_password("pass2", r1["password_hash"], r1["password_salt"])


class TestJWT:
    def test_create_and_verify_token(self, env):
        import importlib, app as app_module
        importlib.reload(app_module)
        token = app_module.create_token("someone@test.com")
        assert app_module.verify_token(token) == "someone@test.com"

    def test_tampered_token_is_rejected(self, env):
        import importlib, app as app_module
        importlib.reload(app_module)
        token = app_module.create_token("someone@test.com")
        bad = token[:-4] + "XXXX"
        assert app_module.verify_token(bad) is None

    def test_garbage_token_is_rejected(self, env):
        import importlib, app as app_module
        importlib.reload(app_module)
        assert app_module.verify_token("not.a.token") is None


class TestLoginEndpoint:
    def test_login_with_correct_password_sets_cookie(self, client, env):
        resp = client.post(
            "/login",
            data={"email": env["user_email"], "password": env["password"]},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        assert "token" in resp.cookies

    def test_login_with_wrong_password_returns_error(self, client, env):
        resp = client.post(
            "/login",
            data={"email": env["user_email"], "password": "wrongpass"},
            follow_redirects=False,
        )
        assert resp.status_code == 200  # re-renders login page
        assert "token" not in resp.cookies

    def test_first_login_creates_user_record(self, client, env):
        from auth import get_user_password
        email = "newuser@test.com"
        client.post(
            "/login",
            data={"email": email, "password": env["password"]},
            follow_redirects=False,
        )
        assert get_user_password(email) is not None

    def test_second_login_uses_stored_hash(self, client, env):
        email = "returning@test.com"
        # First login — stores hash
        r1 = client.post(
            "/login",
            data={"email": email, "password": env["password"]},
            follow_redirects=False,
        )
        # Second login — verifies against stored hash
        r2 = client.post(
            "/login",
            data={"email": email, "password": env["password"]},
            follow_redirects=False,
        )
        assert "token" in r1.cookies
        assert "token" in r2.cookies

    def test_logout_clears_cookie(self, client, auth_cookie):
        resp = client.get("/logout", cookies=auth_cookie, follow_redirects=False)
        assert resp.status_code in (302, 303)
        # Cookie cleared (empty value or absent)
        cookie_val = resp.cookies.get("token", "")
        assert cookie_val == "" or "token" not in resp.cookies


class TestAccessControl:
    def test_chat_requires_auth(self, client):
        resp = client.get("/chat", follow_redirects=False)
        assert resp.status_code in (302, 303)

    def test_chat_accessible_with_token(self, client, auth_cookie):
        resp = client.get("/chat", cookies=auth_cookie)
        assert resp.status_code == 200

    def test_kb_requires_admin(self, client, auth_cookie):
        resp = client.get("/kb", cookies=auth_cookie, follow_redirects=False)
        assert resp.status_code in (302, 303, 403)

    def test_kb_accessible_to_admin(self, client, admin_cookie):
        resp = client.get("/kb", cookies=admin_cookie)
        assert resp.status_code == 200

    def test_analytics_requires_admin(self, client, auth_cookie, follow_redirects=False):
        resp = client.get("/analytics", cookies=auth_cookie, follow_redirects=False)
        assert resp.status_code in (302, 303, 403)

    def test_health_requires_no_auth(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_login_page_requires_no_auth(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200
