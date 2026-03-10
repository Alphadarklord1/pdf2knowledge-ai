from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent / "kb_app.db"

DEFAULT_SETTINGS = {
    "privacy_masking_enabled": True,
    "persist_uploaded_files": False,
    "allow_openai_enhancement": True,
    "release_stage": "beta",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_password(password: str, *, iterations: int = 210000) -> str:
    salt = os.urandom(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(derived).decode()}"


def verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash.startswith("pbkdf2_sha256$"):
        return False
    try:
        _, iter_str, salt_b64, digest_b64 = stored_hash.split("$", 3)
        iterations = int(iter_str)
        salt = base64.b64decode(salt_b64.encode())
        digest = base64.b64decode(digest_b64.encode())
    except Exception:
        return False
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(derived, digest)


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _column_exists(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(str(row["name"]) == column_name for row in rows)


def init_db() -> None:
    with connect_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
              user_id TEXT PRIMARY KEY,
              display_name TEXT NOT NULL,
              password_hash TEXT NOT NULL,
              role TEXT NOT NULL,
              status TEXT NOT NULL,
              created_at_utc TEXT NOT NULL,
              updated_at_utc TEXT NOT NULL,
              failed_login_attempts INTEGER NOT NULL DEFAULT 0,
              last_login_at_utc TEXT
            );

            CREATE TABLE IF NOT EXISTS app_settings (
              key TEXT PRIMARY KEY,
              value_json TEXT NOT NULL,
              updated_at_utc TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_events (
              event_id TEXT PRIMARY KEY,
              timestamp_utc TEXT NOT NULL,
              user_id TEXT NOT NULL,
              action TEXT NOT NULL,
              result TEXT NOT NULL,
              details_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS feedback_items (
              feedback_id TEXT PRIMARY KEY,
              created_at_utc TEXT NOT NULL,
              user_id TEXT NOT NULL,
              category TEXT NOT NULL,
              message TEXT NOT NULL,
              status TEXT NOT NULL,
              reviewer_user_id TEXT,
              reviewed_at_utc TEXT
            );

            CREATE TABLE IF NOT EXISTS share_items (
              share_id TEXT PRIMARY KEY,
              created_at_utc TEXT NOT NULL,
              user_id TEXT NOT NULL,
              share_code TEXT NOT NULL,
              title TEXT NOT NULL,
              share_note TEXT NOT NULL,
              source_filename TEXT NOT NULL,
              payload_zip BLOB
            );
            """
        )
        if not _column_exists(conn, "share_items", "payload_zip"):
            conn.execute("ALTER TABLE share_items ADD COLUMN payload_zip BLOB")
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO app_settings(key, value_json, updated_at_utc) VALUES (?, ?, ?)",
                (key, json.dumps(value), utc_now()),
            )
        seed_demo_users(conn)


def seed_demo_users(conn: sqlite3.Connection) -> None:
    users = [
        ("kb_admin", "KB Admin", "Admin@123", "supervisor", "active"),
        ("kb_reviewer", "KB Reviewer", "Reviewer@123", "operator", "active"),
        ("kb_auditor", "KB Auditor", "Auditor@123", "auditor", "active"),
    ]
    now = utc_now()
    for user_id, display_name, password, role, status in users:
        exists = conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if exists:
            continue
        conn.execute(
            "INSERT INTO users(user_id, display_name, password_hash, role, status, created_at_utc, updated_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, display_name, hash_password(password), role, status, now, now),
        )


def append_audit_event(user_id: str, action: str, result: str, details: dict[str, Any] | None = None) -> None:
    with connect_db() as conn:
        conn.execute(
            "INSERT INTO audit_events(event_id, timestamp_utc, user_id, action, result, details_json) VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), utc_now(), user_id, action, result, json.dumps(details or {}, ensure_ascii=False)),
        )


def authenticate_user(user_id: str, password: str) -> dict[str, Any] | None:
    with connect_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id.strip(),)).fetchone()
        if row is None:
            return None
        if row["status"] != "active":
            return None
        if not verify_password(password, row["password_hash"]):
            conn.execute(
                "UPDATE users SET failed_login_attempts = failed_login_attempts + 1, updated_at_utc = ? WHERE user_id = ?",
                (utc_now(), row["user_id"]),
            )
            return None
        conn.execute(
            "UPDATE users SET failed_login_attempts = 0, last_login_at_utc = ?, updated_at_utc = ? WHERE user_id = ?",
            (utc_now(), utc_now(), row["user_id"]),
        )
        return dict(row)


def create_signup_user(user_id: str, display_name: str, password: str) -> tuple[bool, str]:
    user_id = user_id.strip()
    display_name = display_name.strip()
    if len(user_id) < 3 or len(display_name) < 2 or len(password) < 8:
        return False, "User ID, display name, and password do not meet minimum requirements."
    with connect_db() as conn:
        exists = conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if exists:
            return False, "User already exists."
        now = utc_now()
        conn.execute(
            "INSERT INTO users(user_id, display_name, password_hash, role, status, created_at_utc, updated_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, display_name, hash_password(password), "operator", "pending", now, now),
        )
    append_audit_event(user_id, "register", "pending", {"role": "operator"})
    return True, "Account created and waiting for supervisor approval."


def require_role(role: str, allowed: tuple[str, ...]) -> None:
    if role not in allowed:
        raise PermissionError(f"Role {role} is not permitted for this action.")


def list_users() -> list[dict[str, Any]]:
    with connect_db() as conn:
        rows = conn.execute(
            "SELECT user_id, display_name, role, status, created_at_utc, updated_at_utc, failed_login_attempts, last_login_at_utc FROM users ORDER BY created_at_utc DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def update_user_status(user_id: str, status: str) -> None:
    with connect_db() as conn:
        conn.execute("UPDATE users SET status = ?, updated_at_utc = ? WHERE user_id = ?", (status, utc_now(), user_id))


def reset_user_password(user_id: str, new_password: str) -> None:
    with connect_db() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, updated_at_utc = ?, failed_login_attempts = 0 WHERE user_id = ?",
            (hash_password(new_password), utc_now(), user_id),
        )


def get_settings() -> dict[str, Any]:
    values = DEFAULT_SETTINGS.copy()
    with connect_db() as conn:
        rows = conn.execute("SELECT key, value_json FROM app_settings").fetchall()
    for row in rows:
        values[row["key"]] = json.loads(row["value_json"])
    return values


def set_setting(key: str, value: Any) -> None:
    with connect_db() as conn:
        conn.execute(
            "INSERT INTO app_settings(key, value_json, updated_at_utc) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at_utc = excluded.updated_at_utc",
            (key, json.dumps(value), utc_now()),
        )


def list_audit_events(limit: int = 100) -> list[dict[str, Any]]:
    with connect_db() as conn:
        rows = conn.execute(
            "SELECT event_id, timestamp_utc, user_id, action, result, details_json FROM audit_events ORDER BY timestamp_utc DESC LIMIT ?",
            (limit,),
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["details"] = json.loads(item.pop("details_json") or "{}")
        items.append(item)
    return items


def submit_feedback(user_id: str, category: str, message: str) -> tuple[bool, str]:
    category = category.strip() or "general"
    message = message.strip()
    if len(message) < 8:
        return False, "Feedback message is too short."
    with connect_db() as conn:
        conn.execute(
            """
            INSERT INTO feedback_items(
              feedback_id, created_at_utc, user_id, category, message, status, reviewer_user_id, reviewed_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), utc_now(), user_id, category, message, "open", None, None),
        )
    append_audit_event(user_id, "submit_feedback", "success", {"category": category})
    return True, "Feedback submitted."


def list_feedback_items(limit: int = 100) -> list[dict[str, Any]]:
    with connect_db() as conn:
        rows = conn.execute(
            """
            SELECT feedback_id, created_at_utc, user_id, category, message, status, reviewer_user_id, reviewed_at_utc
            FROM feedback_items
            ORDER BY created_at_utc DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def review_feedback_item(feedback_id: str, reviewer_user_id: str, status: str) -> None:
    with connect_db() as conn:
        conn.execute(
            """
            UPDATE feedback_items
            SET status = ?, reviewer_user_id = ?, reviewed_at_utc = ?
            WHERE feedback_id = ?
            """,
            (status, reviewer_user_id, utc_now(), feedback_id),
        )


def export_feedback_jsonl(limit: int = 500) -> bytes:
    rows = list_feedback_items(limit=limit)
    payload = "\n".join(json.dumps(item, ensure_ascii=False) for item in rows)
    return payload.encode("utf-8")


def generate_share_code() -> str:
    return uuid.uuid4().hex[:8].upper()


def create_share_item(user_id: str, title: str, *, share_note: str = "", source_filename: str = "", payload_zip: bytes, share_code: str | None = None) -> dict[str, Any]:
    share_id = str(uuid.uuid4())
    share_code = share_code or generate_share_code()
    item = {
        "share_id": share_id,
        "created_at_utc": utc_now(),
        "user_id": user_id,
        "share_code": share_code,
        "title": title.strip() or "KB Share Package",
        "share_note": share_note.strip(),
        "source_filename": source_filename.strip(),
    }
    with connect_db() as conn:
        conn.execute(
            """
            INSERT INTO share_items(share_id, created_at_utc, user_id, share_code, title, share_note, source_filename, payload_zip)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["share_id"],
                item["created_at_utc"],
                item["user_id"],
                item["share_code"],
                item["title"],
                item["share_note"],
                item["source_filename"],
                sqlite3.Binary(payload_zip),
            ),
        )
    append_audit_event(user_id, "create_share", "success", {"share_code": share_code, "title": item["title"]})
    return item


def list_share_items(limit: int = 100) -> list[dict[str, Any]]:
    with connect_db() as conn:
        rows = conn.execute(
            """
            SELECT share_id, created_at_utc, user_id, share_code, title, share_note, source_filename, LENGTH(payload_zip) AS payload_size
            FROM share_items
            ORDER BY created_at_utc DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_share_payload(share_id: str) -> bytes | None:
    with connect_db() as conn:
        row = conn.execute("SELECT payload_zip FROM share_items WHERE share_id = ?", (share_id,)).fetchone()
    if row is None:
        return None
    value = row["payload_zip"]
    return bytes(value) if value is not None else None
