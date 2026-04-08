"""Per-user password hashing and storage for FrontDesk AI.

Uses PBKDF2-HMAC-SHA256 with 600k iterations (OWASP 2024 recommendation).
Stores hashed passwords in the history.db database (auth concern, separate
from business data in frontdesk_tools.db).
"""

import hashlib
import os
import sqlite3
from contextvars import ContextVar

from tools import HISTORY_DB

# Secure bridge: app.py sets this after JWT verification; tools read it.
# The LLM never sees or influences this value.
current_user_email: ContextVar[str] = ContextVar("current_user_email")

_ITERATIONS = 600_000
_HASH_ALGO = "sha256"
_SALT_BYTES = 32


def _get_auth_db() -> sqlite3.Connection:
    """Connect to history.db and ensure the users table exists."""
    os.makedirs(os.path.dirname(HISTORY_DB), exist_ok=True)
    conn = sqlite3.connect(HISTORY_DB)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS users (
            email          TEXT PRIMARY KEY,
            password_hash  TEXT NOT NULL,
            password_salt  TEXT NOT NULL,
            created_at     TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
        )"""
    )
    conn.commit()
    return conn


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    """Hash a password with PBKDF2-HMAC-SHA256.

    Returns (hash_hex, salt_hex).
    """
    if salt is None:
        salt = os.urandom(_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac(_HASH_ALGO, password.encode(), salt, _ITERATIONS)
    return dk.hex(), salt.hex()


def verify_password(password: str, stored_hash: str, stored_salt: str) -> bool:
    """Verify a password against a stored hash and salt."""
    salt = bytes.fromhex(stored_salt)
    dk = hashlib.pbkdf2_hmac(_HASH_ALGO, password.encode(), salt, _ITERATIONS)
    return dk.hex() == stored_hash


def get_user_password(email: str) -> dict | None:
    """Look up a user's stored password hash. Returns dict with hash/salt or None."""
    conn = _get_auth_db()
    try:
        row = conn.execute(
            "SELECT password_hash, password_salt FROM users WHERE email = ?",
            (email,),
        ).fetchone()
        if row:
            return {"password_hash": row["password_hash"], "password_salt": row["password_salt"]}
        return None
    finally:
        conn.close()


def set_user_password(email: str, password: str) -> None:
    """Store (or update) a user's hashed password."""
    hash_hex, salt_hex = hash_password(password)
    conn = _get_auth_db()
    try:
        conn.execute(
            "INSERT INTO users (email, password_hash, password_salt) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(email) DO UPDATE SET "
            "  password_hash = excluded.password_hash, "
            "  password_salt = excluded.password_salt, "
            "  updated_at = datetime('now')",
            (email, hash_hex, salt_hex),
        )
        conn.commit()
    finally:
        conn.close()
