"""Authentication, Principal, session management, and access control for Baton.

Implements ROUND3_SPEC sections 1.1 to 1.6 and 1.8.
"""
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import hashlib
import hmac
import json
import os
import secrets
import sys
from typing import Annotated

from fastapi import Request, HTTPException, Depends, status
from fastapi.responses import HTMLResponse, RedirectResponse

from . import db

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "baton-demo-2026")


def get_max_users() -> int:
    return int(os.environ.get("MAX_USERS", "200"))


@dataclass(frozen=True)
class Principal:
    user_id: int
    name: str
    email: str
    case_id: int
    role: str  # approver, leaver, successor, viewer
    is_owner: bool
    holds: frozenset[str]
    is_demo: bool = False

    def __post_init__(self):
        if not isinstance(self.holds, frozenset):
            object.__setattr__(self, "holds", frozenset(self.holds))


def _load_roles_json(data_dir: str = DATA) -> dict:
    with open(os.path.join(data_dir, "roles.json"), encoding="utf-8") as f:
        return json.load(f)["roles"]


def hash_password(password: str) -> str:
    """Hash a password using hashlib.scrypt with a per-user random salt."""
    if len(password) < 10:
        raise ValueError("Password must be at least 10 characters")
    salt = os.urandom(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1)
    return f"{salt.hex()}${derived.hex()}"


def verify_password(password: str, pw_hash: str) -> bool:
    """Verify password against scrypt hash in constant time."""
    try:
        salt_hex, hash_hex = pw_hash.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1)
        return hmac.compare_digest(derived, expected)
    except Exception:
        return False


def create_session(user_id: int, conn=None) -> str:
    """Create a 12-hour session for user_id. Stores only the SHA-256 of the token."""
    if conn is None:
        conn = db.get_conn()
    raw_token = secrets.token_hex(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    created_at = now.isoformat()
    expires_at = (now + timedelta(hours=12)).isoformat()

    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token_hash, user_id, created_at, expires_at)
    )
    conn.commit()
    return raw_token


def delete_session(raw_token: str, conn=None):
    """Delete a session upon logout."""
    if conn is None:
        conn = db.get_conn()
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
    conn.commit()


def get_user_from_token(raw_token: str | None, conn=None) -> dict | None:
    """Look up an active user by session token. Returns None if invalid or expired."""
    if not raw_token:
        return None
    if conn is None:
        conn = db.get_conn()
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    row = conn.execute(
        "SELECT * FROM sessions WHERE token_hash = ?", (token_hash,)
    ).fetchone()
    if not row:
        return None

    now_iso = datetime.now(timezone.utc).isoformat()
    if row["expires_at"] <= now_iso:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        conn.commit()
        return None

    user = conn.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone()
    return dict(user) if user else None


def get_client_ip(request: Request) -> str:
    """Extract client IP using TRUSTED_PROXY_HOPS places from the right of X-Forwarded-For.
    Never uses the first entry (which can be forged by the client).
    Falls back to the socket address.
    """
    try:
        hops = int(os.environ.get("TRUSTED_PROXY_HOPS", "1"))
    except ValueError:
        hops = 1
    if hops < 1:
        hops = 1

    xff = request.headers.get("x-forwarded-for")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if len(parts) > hops:
            return parts[-hops]

    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


def is_demo_account(email: str, conn=None) -> bool:
    """Check if an email belongs to a demo account (is_demo=1)."""
    clean_email = email.lower().strip()
    if clean_email in ("divya@example.com", "meera@example.com", "arjun@example.com", "sameer@example.com"):
        return True
    if conn is None:
        conn = db.get_conn()
    try:
        user = conn.execute("SELECT is_demo FROM users WHERE email = ?", (clean_email,)).fetchone()
        return bool(user and user["is_demo"])
    except Exception:
        return False


def is_login_throttled(email: str, ip: str, conn=None) -> bool:
    """Check if login is throttled:
    - Demo accounts (is_demo=1) are not throttled at all.
    - Count failures per (email, client IP) pair: 5 in 15 minutes.
    - Separate per-IP cap: 20 failures in 15 minutes.
    - Never lock an email on its own.
    """
    if conn is None:
        conn = db.get_conn()

    if is_demo_account(email, conn):
        return False

    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    clean_email = email.lower().strip()

    # 1. Count failures per (email, client IP) pair: 5 in 15 minutes
    if clean_email and ip:
        pair_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM login_attempts WHERE email = ? AND ip = ? AND at >= ?",
            (clean_email, ip, cutoff)
        ).fetchone()
        if pair_row and pair_row["cnt"] >= 5:
            return True

    # 2. Separate per-IP cap: 20 failures in 15 minutes
    if ip:
        ip_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM login_attempts WHERE ip = ? AND at >= ?",
            (ip, cutoff)
        ).fetchone()
        if ip_row and ip_row["cnt"] >= 20:
            return True

    return False


def reset_login_attempts(email: str, ip: str, conn=None):
    """Reset failed login attempts for this email and IP on successful authentication."""
    if conn is None:
        conn = db.get_conn()
    conn.execute(
        "DELETE FROM login_attempts WHERE email = ? AND ip = ?",
        (email.lower().strip(), ip)
    )
    conn.commit()


def is_signup_throttled(ip: str, conn=None) -> bool:
    """Per-IP rate limit for signups (max 10 per hour from one IP)."""
    if conn is None:
        conn = db.get_conn()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM login_attempts WHERE email = '__signup__' AND ip = ? AND at >= ?",
        (ip, cutoff)
    ).fetchone()
    return bool(row and row["cnt"] >= 10)


def record_signup_attempt(ip: str, conn=None):
    """Record signup for rate limiting."""
    if conn is None:
        conn = db.get_conn()
    conn.execute(
        "INSERT INTO login_attempts (email, ip, at) VALUES ('__signup__', ?, ?)",
        (ip, datetime.now(timezone.utc).isoformat())
    )
    conn.commit()


def record_login_attempt(email: str, ip: str, conn=None):
    """Record a failed login attempt for rate limiting."""
    if conn is None:
        conn = db.get_conn()
    conn.execute(
        "INSERT INTO login_attempts (email, ip, at) VALUES (?, ?, ?)",
        (email.lower().strip(), ip, datetime.now(timezone.utc).isoformat())
    )
    conn.commit()


def create_user(email: str, name: str, password: str, is_demo: int = 0, conn=None) -> int:
    """Create a new user, enforcing MAX_USERS and minimum 10 char password."""
    if conn is None:
        conn = db.get_conn()

    count_row = conn.execute("SELECT COUNT(*) as cnt FROM users").fetchone()
    if count_row and count_row["cnt"] >= get_max_users():
        raise ValueError("User registration limit reached")

    clean_email = email.lower().strip()
    existing = conn.execute("SELECT id FROM users WHERE email = ?", (clean_email,)).fetchone()
    if existing:
        raise ValueError("An account with this email already exists")

    pw_hash = hash_password(password)
    cur = conn.execute(
        "INSERT INTO users (email, name, pw_hash, is_demo, created_at) VALUES (?, ?, ?, ?, ?)",
        (clean_email, name.strip(), pw_hash, is_demo, datetime.now(timezone.utc).isoformat())
    )
    conn.commit()
    return cur.lastrowid


def seed_demo_accounts(conn=None):
    """Seed the four demo accounts at boot, members of case 1 only (SPEC 1.4).
    Re-asserts the four demo memberships (role and labels) from code on every boot
    and inside Reset demo.
    """
    if conn is None:
        conn = db.get_conn()

    # Ensure case 1 exists before inserting memberships
    case_1 = conn.execute("SELECT id FROM cases WHERE id = 1").fetchone()
    if not case_1:
        db.load_demo_case()

    roles_data = _load_roles_json()
    demo_accounts = [
        ("divya@example.com", "Divya Menon", "lead", "approver", 1, roles_data["lead"]["holds"]),
        ("meera@example.com", "Meera Joshi", "leaver", "leaver", 0, roles_data["leaver"]["holds"]),
        ("arjun@example.com", "Arjun Rao", "successor", "successor", 0, roles_data["successor"]["holds"]),
        ("sameer@example.com", "Sameer Kulkarni", "finance_ops", "viewer", 0, roles_data["finance_ops"]["holds"]),
    ]

    pw_hash = hash_password(DEMO_PASSWORD)
    now_iso = datetime.now(timezone.utc).isoformat()

    for email, name, role_key, role_name, is_owner, labels in demo_accounts:
        user = conn.execute("SELECT id, is_demo FROM users WHERE email = ?", (email,)).fetchone()
        if not user:
            cur = conn.execute(
                "INSERT INTO users (email, name, pw_hash, is_demo, created_at) VALUES (?, ?, ?, 1, ?)",
                (email, name, pw_hash, now_iso)
            )
            uid = cur.lastrowid
        else:
            uid = user["id"]
            if not user["is_demo"]:
                conn.execute("UPDATE users SET is_demo = 1 WHERE id = ?", (uid,))

        # Re-assert membership in case 1
        membership = conn.execute(
            "SELECT * FROM memberships WHERE case_id = 1 AND user_id = ?", (uid,)
        ).fetchone()
        if not membership:
            conn.execute(
                "INSERT INTO memberships (case_id, user_id, role, is_owner, labels, added_by, added_at) VALUES (?, ?, ?, ?, ?, NULL, ?)",
                (1, uid, role_name, is_owner, json.dumps(sorted(labels)), now_iso)
            )
        else:
            conn.execute(
                "UPDATE memberships SET role = ?, is_owner = ?, labels = ? WHERE case_id = 1 AND user_id = ?",
                (role_name, is_owner, json.dumps(sorted(labels)), uid)
            )

    conn.commit()


def demo_principal(name: str, case_id: int = 1) -> Principal:
    """Return the Principal for the four demo roles reading names and clearance
    from data/roles.json and the case row.
    """
    roles_data = _load_roles_json()

    case_row = None
    try:
        conn = db.get_conn()
        row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if row:
            case_row = dict(row)
    except Exception:
        pass

    normalized = name.lower().strip()
    if normalized in ("lead", "approver", "divya", "divya menon", "divya@example.com"):
        p_name = (case_row and case_row.get("approver")) or "Divya Menon"
        return Principal(
            user_id=1,
            name=p_name,
            email="divya@example.com",
            case_id=case_id,
            role="approver",
            is_owner=True,
            holds=frozenset(roles_data["lead"]["holds"]),
            is_demo=True,
        )
    elif normalized in ("leaver", "meera", "meera joshi", "meera@example.com"):
        p_name = (case_row and case_row.get("leaver")) or "Meera Joshi"
        return Principal(
            user_id=2,
            name=p_name,
            email="meera@example.com",
            case_id=case_id,
            role="leaver",
            is_owner=False,
            holds=frozenset(roles_data["leaver"]["holds"]),
            is_demo=True,
        )
    elif normalized in ("successor", "arjun", "arjun rao", "arjun@example.com"):
        p_name = (case_row and case_row.get("successor")) or "Arjun Rao"
        return Principal(
            user_id=3,
            name=p_name,
            email="arjun@example.com",
            case_id=case_id,
            role="successor",
            is_owner=False,
            holds=frozenset(roles_data["successor"]["holds"]),
            is_demo=True,
        )
    elif normalized in ("finance_ops", "finance", "viewer", "sameer", "sameer kulkarni", "sameer@example.com"):
        return Principal(
            user_id=4,
            name="Sameer Kulkarni",
            email="sameer@example.com",
            case_id=case_id,
            role="viewer",
            is_owner=False,
            holds=frozenset(roles_data["finance_ops"]["holds"]),
            is_demo=True,
        )
    else:
        raise ValueError(f"Unknown demo role/persona: {name}")


def create_invite(case_id: int, role: str, labels: list[str], created_by: int, conn=None) -> str:
    """Create a 7-day single-use invite token."""
    if conn is None:
        conn = db.get_conn()
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(days=7)).isoformat()

    conn.execute(
        "INSERT INTO invites (token_hash, case_id, role, labels, created_by, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (token_hash, case_id, role, json.dumps(sorted(labels)), created_by, now.isoformat(), expires_at)
    )
    conn.commit()
    return token


def get_invite(token: str, conn=None) -> dict | None:
    """Look up an invite token. Returns None if not found, expired, or used."""
    if conn is None:
        conn = db.get_conn()
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    row = conn.execute("SELECT * FROM invites WHERE token_hash = ?", (token_hash,)).fetchone()
    if not row:
        return None
    d = dict(row)
    if d.get("used_at"):
        return None
    now_iso = datetime.now(timezone.utc).isoformat()
    if d["expires_at"] <= now_iso:
        return None
    d["labels"] = json.loads(d["labels"])
    return d


def redeem_invite(token: str, user_id: int, conn=None) -> int:
    """Redeem an invite for a user. Returns case_id."""
    if conn is None:
        conn = db.get_conn()
    invite = get_invite(token, conn=conn)
    if not invite:
        raise ValueError("Invalid or expired invite token")

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now_iso = datetime.now(timezone.utc).isoformat()

    conn.execute(
        "INSERT OR REPLACE INTO memberships (case_id, user_id, role, is_owner, labels, added_by, added_at) "
        "VALUES (?, ?, ?, 0, ?, ?, ?)",
        (invite["case_id"], user_id, invite["role"], json.dumps(invite["labels"]), invite["created_by"], now_iso)
    )
    conn.execute("UPDATE invites SET used_at = ? WHERE token_hash = ?", (now_iso, token_hash))
    conn.commit()
    return invite["case_id"]


def login_as(client, name: str) -> str:
    """Sign in via the real /login endpoint for a demo account or email."""
    normalized = name.lower().strip()
    email_map = {
        "lead": "divya@example.com",
        "approver": "divya@example.com",
        "divya": "divya@example.com",
        "divya menon": "divya@example.com",
        "leaver": "meera@example.com",
        "meera": "meera@example.com",
        "meera joshi": "meera@example.com",
        "successor": "arjun@example.com",
        "arjun": "arjun@example.com",
        "arjun rao": "arjun@example.com",
        "finance_ops": "sameer@example.com",
        "finance": "sameer@example.com",
        "viewer": "sameer@example.com",
        "sameer": "sameer@example.com",
        "sameer kulkarni": "sameer@example.com",
    }
    email = email_map.get(normalized, name)
    resp = client.post(
        "/login",
        data={"email": email, "password": DEMO_PASSWORD},
        follow_redirects=False,
    )
    return resp
