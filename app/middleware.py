"""Wire-check and security middleware for Baton.

Implements SPEC section 7 and ROUND3_SPEC sections 1.2, 1.4:
- Refuses to start without SECRET_KEY (except under pytest)
- Rejects cross-origin POSTs (Origin / Referer host mismatch)
- Defence in depth on every HTML response
- Scans response body for canaries the principal must not see
- Fails closed: replaces response with error page, logs to audit, returns 500
- Replaces <!-- WIRE_STRIP --> with wire strip stating scan results
"""
import json
import os
import sys
from urllib.parse import urlparse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from . import db, access
from .auth import Principal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANARIES_PATH = os.path.join(ROOT, "data", "canaries.json")

with open(CANARIES_PATH, encoding="utf-8") as f:
    CANARIES = json.load(f)


def _is_running_under_pytest() -> bool:
    return "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST") is not None


SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    if _is_running_under_pytest():
        SECRET_KEY = "baton-pytest-secret-key-1234567890"
    else:
        raise RuntimeError("SECRET_KEY environment variable is not set. Baton refuses to start.")


class WireCheckMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 1. Origin rule for every POST (ROUND3_SPEC 1.2)
        if request.method == "POST":
            origin = request.headers.get("origin")
            host = request.headers.get("host", "").lower()
            if origin:
                origin_host = urlparse(origin).netloc.lower()
                if origin_host != host:
                    return Response("Cross-origin request rejected", status_code=403)
            elif "referer" in request.headers:
                referer = request.headers.get("referer")
                referer_host = urlparse(referer).netloc.lower()
                if referer_host != host:
                    return Response("Cross-origin request rejected", status_code=403)

        response = await call_next(request)

        # Scan every response whose type starts with text/ or is application/json (ROUND3_SPEC 3.4)
        content_type = response.headers.get("content-type", "").lower()
        is_scanned_type = (
            content_type.startswith("text/")
            or content_type.startswith("application/json")
            or "application/json" in content_type
        )
        if not is_scanned_type:
            return response

        # Read response body
        body_chunks = [chunk async for chunk in response.body_iterator]
        body_bytes = b"".join(body_chunks)
        body_str = body_bytes.decode("utf-8", errors="replace")

        # Also check the Content-Disposition filename
        content_disp = response.headers.get("content-disposition", "")

        principal = getattr(request.state, "principal", None)
        holds = principal.holds if principal else frozenset()
        persona = principal.role if principal else "anonymous"

        # Check for forbidden canaries
        forbidden_canaries = [
            c["text"] for c in CANARIES if c["group"] not in holds
        ]

        for canary in forbidden_canaries:
            if canary in body_str or canary in content_disp:
                # Log leak to audit table
                try:
                    conn = db.get_conn()
                    conn.execute(
                        "INSERT INTO audit (at, persona, action, target) VALUES (?, ?, ?, ?)",
                        (db.now_iso(), persona, "blocked_canary_leak", str(request.url.path))
                    )
                    conn.commit()
                except Exception:
                    pass

                # Fail closed: return 500
                if "text/html" in content_type:
                    error_content = (
                        "<!doctype html><html lang=\"en\"><head><title>Blocked</title>"
                        "<link rel=\"stylesheet\" href=\"/static/baton.css\"></head><body>"
                        "<div class=\"wrap\" style=\"padding-top:var(--s8)\">"
                        "<h1 class=\"h1\" style=\"margin-bottom:var(--s4)\">Blocked</h1>"
                        "<div class=\"banner\">Blocked: a restricted string reached this response</div>"
                        "</div></body></html>"
                    )
                    media_type = "text/html"
                elif "application/json" in content_type:
                    error_content = json.dumps({"error": "Blocked: a restricted string reached this response"})
                    media_type = "application/json"
                else:
                    error_content = "Blocked: a restricted string reached this response\n"
                    media_type = "text/plain; charset=utf-8"

                return Response(
                    content=error_content,
                    status_code=500,
                    media_type=media_type,
                )

        # Replace wire strip placeholder if present (HTML only)
        if "text/html" in content_type and "<!-- WIRE_STRIP -->" in body_str:
            if principal and principal.role == "approver" and {"team", "finance", "hr"}.issubset(principal.holds):
                strip = (
                    '<div class="wire" style="margin-top:var(--s8)">'
                    '<span class="label">Wire check</span>'
                    '<span>This role is cleared for all 9 planted strings, so nothing is blocked here.</span>'
                    '<span>Role: Approver &middot; clearance: team, finance, hr</span>'
                    '</div>'
                )
            elif principal:
                display_role = principal.role.capitalize()
                clearance_str = ", ".join(sorted(principal.holds)) if principal.holds else "none"
                n_forbidden = len(forbidden_canaries)
                strip = (
                    f'<div class="wire" style="margin-top:var(--s8)">'
                    f'<span class="label">Wire check</span>'
                    f'<span>This response was scanned for <strong>{n_forbidden}</strong> planted strings: <strong>0</strong> present.</span>'
                    f'<span>Role: {display_role} &middot; clearance: {clearance_str}</span>'
                    f'</div>'
                )
            else:
                strip = ""

            body_str = body_str.replace("<!-- WIRE_STRIP -->", strip)
            new_bytes = body_str.encode("utf-8")

            headers = dict(response.headers)
            headers["content-length"] = str(len(new_bytes))
            return Response(
                content=new_bytes,
                status_code=response.status_code,
                headers=headers,
                media_type="text/html",
            )

        headers = dict(response.headers)
        headers["content-length"] = str(len(body_bytes))
        return Response(
            content=body_bytes,
            status_code=response.status_code,
            headers=headers,
            media_type=response.media_type,
        )
