"""Wire-check middleware for Baton.

Implements SPEC section 7:
- Defence in depth on every HTML response
- Scans response body for canaries the persona must not see
- Fails closed: replaces response with error page, logs to audit, returns 500
- Replaces <!-- WIRE_STRIP --> with wire strip stating scan results
"""
import json
import os
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from itsdangerous import Signer

from . import db, access

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANARIES_PATH = os.path.join(ROOT, "data", "canaries.json")

with open(CANARIES_PATH, encoding="utf-8") as f:
    CANARIES = json.load(f)

SECRET_KEY = os.environ.get("SECRET_KEY", "baton-dev-secret-key-123")
signer = Signer(SECRET_KEY)


def get_persona_from_request(request: Request) -> str:
    """Extract and verify the persona from the signed baton_role cookie."""
    cookie = request.cookies.get("baton_role")
    if cookie:
        try:
            return signer.unsign(cookie.encode("utf-8")).decode("utf-8")
        except Exception:
            return "lead"
    return "lead"


class WireCheckMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Only process HTML responses
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type:
            return response

        # Read response body
        body_chunks = [chunk async for chunk in response.body_iterator]
        body_bytes = b"".join(body_chunks)
        body_str = body_bytes.decode("utf-8", errors="replace")

        persona = get_persona_from_request(request)
        holds = access.persona_holds(persona)

        # 1. Check for forbidden canaries
        forbidden_canaries = [
            c["text"] for c in CANARIES if c["group"] not in holds
        ]

        for canary in forbidden_canaries:
            if canary in body_str:
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
                error_html = (
                    "<!doctype html><html lang=\"en\"><head><title>Blocked</title>"
                    "<link rel=\"stylesheet\" href=\"/static/baton.css\"></head><body>"
                    "<div class=\"wrap\" style=\"padding-top:var(--s8)\">"
                    "<h1 class=\"h1\" style=\"margin-bottom:var(--s4)\">Blocked</h1>"
                    "<div class=\"banner\">Blocked: a restricted string reached this response</div>"
                    "</div></body></html>"
                )
                return Response(
                    content=error_html,
                    status_code=500,
                    media_type="text/html",
                )

        # 2. If clean, append/inject the wire strip
        if "<!-- WIRE_STRIP -->" in body_str:
            if persona == "lead":
                strip = (
                    '<div class="wire" style="margin-top:var(--s8)">'
                    '<span class="label">Wire check</span>'
                    '<span>This role is cleared for all 9 planted strings, so nothing is blocked here.</span>'
                    '<span>Role: Approver &middot; clearance: team, finance, hr</span>'
                    '</div>'
                )
            else:
                display_role = access.persona_display(persona).split(" - ")[0]
                clearance_str = ", ".join(sorted(holds))
                n_forbidden = len(forbidden_canaries)
                strip = (
                    f'<div class="wire" style="margin-top:var(--s8)">'
                    f'<span class="label">Wire check</span>'
                    f'<span>This response was scanned for <strong>{n_forbidden}</strong> planted strings: <strong>0</strong> present.</span>'
                    f'<span>Role: {display_role} &middot; clearance: {clearance_str}</span>'
                    f'</div>'
                )

            body_str = body_str.replace("<!-- WIRE_STRIP -->", strip)
            new_bytes = body_str.encode("utf-8")

            # Reconstruct response with updated content-length
            headers = dict(response.headers)
            headers["content-length"] = str(len(new_bytes))
            return Response(
                content=new_bytes,
                status_code=response.status_code,
                headers=headers,
                media_type="text/html",
            )

        # Return unmodified body if no placeholder
        headers = dict(response.headers)
        headers["content-length"] = str(len(body_bytes))
        return Response(
            content=body_bytes,
            status_code=response.status_code,
            headers=headers,
            media_type=response.media_type,
        )
