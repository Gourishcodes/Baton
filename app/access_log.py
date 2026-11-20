"""Request-scoped collector and repository helpers for Baton Access Log.

Implements ROUND3_SPEC section 1.7:
- One row per response that delivered labelled items to a person: who, when, route,
  and delivered items as [{kind, id, labels}]. No claim or line text.
- Item routes log denied attempts (action = 'denied').
- Clearance changes (action = 'clearance_change') and invite creations (action = 'invite_created').
- Exports are logged (action = 'export').
"""
import contextvars
import json
from datetime import datetime, timezone
from typing import Any

from . import db

# Request-scoped collector
_collector_var: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("access_log_collector", default=None)


def get_collector() -> dict[str, Any] | None:
    """Retrieve the current request's access collector."""
    return _collector_var.get()


def set_collector_principal(user_id: int | None, case_id: int | None):
    """Set the user_id and case_id on the active collector."""
    collector = _collector_var.get()
    if collector is not None:
        collector["user_id"] = user_id
        collector["case_id"] = case_id


def record_delivered_item(kind: str, item_id: int | str, labels: list[str]):
    """Record a delivered item in the request-scoped collector.
    Stores id, kind, and labels only; never text.
    """
    collector = _collector_var.get()
    if collector is None:
        return
    key = (kind, str(item_id))
    if key not in collector["seen_keys"]:
        collector["seen_keys"].add(key)
        collector["items"].append({
            "kind": kind,
            "id": item_id,
            "labels": list(labels),
        })
        for l in labels:
            collector["labels_seen"].add(l)


def log_denied(case_id: int, user_id: int | None, route: str, kind: str, item_id: int | str):
    """Log an explicit denied access attempt on an item route."""
    conn = db.get_conn()
    now_iso = datetime.now(timezone.utc).isoformat()
    items_json = json.dumps([{"kind": kind, "id": item_id, "labels": []}])
    labels_seen_json = json.dumps([])
    conn.execute(
        "INSERT INTO access_log (at, user_id, case_id, route, action, items, labels_seen) "
        "VALUES (?, ?, ?, ?, 'denied', ?, ?)",
        (now_iso, user_id, case_id, route, items_json, labels_seen_json),
    )
    conn.commit()
    collector = _collector_var.get()
    if collector is not None:
        collector["action"] = "denied"


def log_clearance_change(case_id: int, user_id: int, route: str, target_user_id: int, role: str, labels: list[str]):
    """Log a member clearance/role change."""
    conn = db.get_conn()
    now_iso = datetime.now(timezone.utc).isoformat()
    items_json = json.dumps([{"kind": "membership", "target_user_id": target_user_id, "role": role, "labels": sorted(labels)}])
    labels_seen_json = json.dumps(sorted(labels))
    conn.execute(
        "INSERT INTO access_log (at, user_id, case_id, route, action, items, labels_seen) "
        "VALUES (?, ?, ?, ?, 'clearance_change', ?, ?)",
        (now_iso, user_id, case_id, route, items_json, labels_seen_json),
    )
    conn.commit()
    collector = _collector_var.get()
    if collector is not None:
        collector["action"] = "clearance_change"


def log_invite_created(case_id: int, user_id: int, route: str, token: str, role: str, labels: list[str]):
    """Log an invite creation action."""
    conn = db.get_conn()
    now_iso = datetime.now(timezone.utc).isoformat()
    items_json = json.dumps([{"kind": "invite", "role": role, "labels": sorted(labels)}])
    labels_seen_json = json.dumps(sorted(labels))
    conn.execute(
        "INSERT INTO access_log (at, user_id, case_id, route, action, items, labels_seen) "
        "VALUES (?, ?, ?, ?, 'invite_created', ?, ?)",
        (now_iso, user_id, case_id, route, items_json, labels_seen_json),
    )
    conn.commit()
    collector = _collector_var.get()
    if collector is not None:
        collector["action"] = "invite_created"


def get_sources_seen_counts(case_id: int) -> dict[str, int]:
    """Return a mapping of source_id -> count of distinct people who have seen it."""
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT user_id, items FROM access_log WHERE case_id = ? AND action IN ('view', 'export')",
        (case_id,)
    ).fetchall()
    users_by_source: dict[str, set[int]] = {}
    for r in rows:
        uid = r["user_id"]
        if not uid:
            continue
        try:
            items = json.loads(r["items"]) if isinstance(r["items"], str) else (r["items"] or [])
        except Exception:
            continue
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict) and it.get("kind") == "source":
                    sid = str(it.get("id"))
                    users_by_source.setdefault(sid, set()).add(uid)

    collector = _collector_var.get()
    if collector and collector.get("user_id"):
        uid = collector["user_id"]
        for it in collector.get("items", []):
            if isinstance(it, dict) and it.get("kind") == "source":
                sid = str(it.get("id"))
                users_by_source.setdefault(sid, set()).add(uid)

    return {sid: len(uids) for sid, uids in users_by_source.items()}


def get_who_has_seen_label(case_id: int, label: str) -> list[dict]:
    """Return each person who has seen items labelled `label`, their count, and last time."""
    conn = db.get_conn()
    rows = conn.execute(
        """SELECT a.user_id, a.at, a.labels_seen, u.name, u.email
           FROM access_log a
           JOIN users u ON a.user_id = u.id
           WHERE a.case_id = ? AND a.action IN ('view', 'export')
           ORDER BY a.at ASC""",
        (case_id,)
    ).fetchall()
    by_user: dict[int, dict] = {}
    for r in rows:
        try:
            labels = json.loads(r["labels_seen"]) if isinstance(r["labels_seen"], str) else (r["labels_seen"] or [])
        except Exception:
            labels = []
        if label in labels:
            uid = r["user_id"]
            if uid not in by_user:
                by_user[uid] = {
                    "name": r["name"],
                    "email": r["email"],
                    "count": 0,
                    "last_seen": r["at"]
                }
            by_user[uid]["count"] += 1
            by_user[uid]["last_seen"] = r["at"]

    result = []
    for uid, data in by_user.items():
        at_str = data["last_seen"][:16].replace("T", " ")
        result.append({
            "name": data["name"],
            "email": data["email"],
            "count": data["count"],
            "last_seen": at_str,
        })
    return sorted(result, key=lambda x: x["count"], reverse=True)


def get_access_logs(case_id: int) -> list[dict]:
    """Return full access log entries for a case."""
    conn = db.get_conn()
    rows = conn.execute(
        """SELECT a.*, u.name as user_name
           FROM access_log a
           LEFT JOIN users u ON a.user_id = u.id
           WHERE a.case_id = ?
           ORDER BY a.id DESC""",
        (case_id,)
    ).fetchall()
    result = []
    for r in rows:
        try:
            items = json.loads(r["items"]) if isinstance(r["items"], str) else (r["items"] or [])
        except Exception:
            items = []
        try:
            labels = json.loads(r["labels_seen"]) if isinstance(r["labels_seen"], str) else (r["labels_seen"] or [])
        except Exception:
            labels = []
        item_count = len(items) if isinstance(items, list) else (1 if isinstance(items, dict) else 0)
        at_str = r["at"][:16].replace("T", " ")
        result.append({
            "id": r["id"],
            "at": r["at"],
            "at_formatted": at_str,
            "user_id": r["user_id"],
            "user_name": r["user_name"] or "Unknown",
            "route": r["route"] or "",
            "action": r["action"] or "view",
            "item_count": item_count,
            "labels": labels,
        })
    return result


class AccessLogMiddleware:
    """Pure ASGI middleware that tracks request-scoped access collector and writes log after response."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        collector = {
            "user_id": None,
            "case_id": None,
            "route": scope.get("path", ""),
            "action": "view",
            "items": [],
            "seen_keys": set(),
            "labels_seen": set(),
        }
        token = _collector_var.set(collector)
        status_code = None

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 200)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
            if status_code is not None and status_code < 400:
                if collector["action"] == "view" and collector["items"] and collector["user_id"]:
                    try:
                        conn = db.get_conn()
                        now_iso = datetime.now(timezone.utc).isoformat()
                        conn.execute(
                            "INSERT INTO access_log (at, user_id, case_id, route, action, items, labels_seen) "
                            "VALUES (?, ?, ?, ?, 'view', ?, ?)",
                            (
                                now_iso,
                                collector["user_id"],
                                collector["case_id"],
                                collector["route"],
                                json.dumps(collector["items"]),
                                json.dumps(sorted(collector["labels_seen"])),
                            )
                        )
                        conn.commit()
                    except Exception:
                        pass
        finally:
            _collector_var.reset(token)
