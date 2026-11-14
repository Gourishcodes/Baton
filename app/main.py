"""Main FastAPI application for Baton.

Follows docs/SPEC.md sections 10, 7, and docs/DESIGN.md.
"""
from contextlib import asynccontextmanager
import json
import os
from typing import Annotated

from fastapi import FastAPI, Request, Form, Response, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import Signer

from . import db, access, repo, review, extract
from .middleware import WireCheckMiddleware, get_persona_from_request, SECRET_KEY

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
signer = Signer(SECRET_KEY)

templates = Jinja2Templates(directory=os.path.join(ROOT, "templates"))


def ensure_demo_seeded():
    """Ensure case 1 is initialized and seeded for demo."""
    db.load_demo_case()
    run = repo.get_latest_run(1)
    if not run:
        try:
            run_id = extract.extract_for_case(1, mode="cache_only")
            review.seed_demo_decisions(1)
            review.create_version(1, approved_by="Divya Menon (seeded)", seeded=True)
        except Exception as e:
            print(f"Seeding demo case notice: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_demo_seeded()
    yield


app = FastAPI(title="Baton", lifespan=lifespan)
app.add_middleware(WireCheckMiddleware)
app.mount("/static", StaticFiles(directory=os.path.join(ROOT, "static")), name="static")


def get_nav_counts(case_id: int, persona: str) -> dict:
    """Compute count badges for the nav tabs."""
    sources = repo.get_sources(case_id, persona)
    claims = repo.get_claims(case_id, persona)
    gaps = repo.get_gaps(case_id, persona)
    conflicts = repo.get_conflicts(case_id, persona)
    versions = repo.get_versions(case_id, persona)
    latest_v = repo.get_latest_version(case_id)

    return {
        "sources": len(sources),
        "claims": len(claims),
        "gaps": len(gaps),
        "conflicts": len(conflicts),
        "version": latest_v or 1,
        "approvals": len(versions) or 1,
    }


def not_found_response():
    """Identical 404 body for any missing or hidden item."""
    html = (
        "<!doctype html><html lang=\"en\"><head><title>Not found</title>"
        "<link rel=\"stylesheet\" href=\"/static/baton.css\"></head><body>"
        "<div class=\"wrap\" style=\"padding-top:var=\"--s8\">"
        "<h1 class=\"h1\">Not found</h1>"
        "<p class=\"meta\" style=\"margin-top:var(--s2)\">The requested item does not exist or is outside your clearance.</p>"
        "</div></body></html>"
    )
    return HTMLResponse(content=html, status_code=404)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    persona = get_persona_from_request(request)
    case = repo.get_case(1, persona)
    counts = get_nav_counts(1, persona) if case else {}
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={"request": request, "role": persona, "case": case, "counts": counts, "active_tab": ""}
    )


@app.post("/role")
def switch_role(request: Request, role: Annotated[str, Form()]):
    """Switch persona role via signed cookie and redirect back."""
    allowed_roles = ["leaver", "lead", "successor", "finance_ops"]
    target_role = role if role in allowed_roles else "lead"

    referer = request.headers.get("referer") or "/case/1/claims"
    response = RedirectResponse(url=referer, status_code=status.HTTP_303_SEE_OTHER)

    signed_val = signer.sign(target_role.encode("utf-8")).decode("utf-8")
    response.set_cookie(
        key="baton_role",
        value=signed_val,
        httponly=True,
        samesite="lax",
    )
    return response


@app.post("/demo/open")
def demo_open():
    """Open the demo case as Approver with drawer open on C1:026."""
    ensure_demo_seeded()

    response = RedirectResponse(
        url="/case/1/claims?open_cite=C1:026",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    signed_val = signer.sign(b"lead").decode("utf-8")
    response.set_cookie(
        key="baton_role",
        value=signed_val,
        httponly=True,
        samesite="lax",
    )
    return response


@app.post("/demo/reset")
def demo_reset():
    """Reset the demo case back to initial seeded state."""
    db.reset()
    ensure_demo_seeded()
    return RedirectResponse(url="/case/1/claims", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/case/{case_id}/sources", response_class=HTMLResponse)
def sources_view(request: Request, case_id: int):
    persona = get_persona_from_request(request)
    case = repo.get_case(case_id, persona)
    if not case:
        return not_found_response()

    sources = repo.get_sources(case_id, persona)
    counts = get_nav_counts(case_id, persona)

    return templates.TemplateResponse(
        request=request,
        name="sources.html",
        context={
            "request": request,
            "role": persona,
            "case": case,
            "counts": counts,
            "sources": sources,
            "active_tab": "sources",
            "parse_error": None,
        }
    )


@app.post("/case/{case_id}/sources")
def upload_source(request: Request, case_id: int):
    """Stub upload source for now; redirect back to sources."""
    return RedirectResponse(url=f"/case/{case_id}/sources", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/case/{case_id}/claims", response_class=HTMLResponse)
def claims_view(
    request: Request,
    case_id: int,
    type: str | None = None,
    needs_review: int | None = None,
    open_cite: str | None = None,
):
    persona = get_persona_from_request(request)
    case = repo.get_case(case_id, persona)
    if not case:
        return not_found_response()

    all_claims = repo.get_claims(case_id, persona)
    counts = get_nav_counts(case_id, persona)

    # Filter counts
    filter_counts = {
        "all": len(all_claims),
        "needs_review": sum(1 for c in all_claims if c.get("visible") and (not c.get("decision") or c.get("decision", {}).get("decision") == "pending")),
        "Decision": sum(1 for c in all_claims if c.get("visible") and c.get("type") == "Decision"),
        "Owner": sum(1 for c in all_claims if c.get("visible") and c.get("type") == "Owner"),
        "Risk": sum(1 for c in all_claims if c.get("visible") and c.get("type") == "Risk"),
        "Dependency": sum(1 for c in all_claims if c.get("visible") and c.get("type") == "Dependency"),
        "OpenQuestion": sum(1 for c in all_claims if c.get("visible") and c.get("type") == "OpenQuestion"),
        "NextAction": sum(1 for c in all_claims if c.get("visible") and c.get("type") == "NextAction"),
    }

    # Filter claims for display
    displayed_claims = all_claims
    if type:
        displayed_claims = [c for c in all_claims if not c.get("visible") or c.get("type") == type]
    elif needs_review:
        displayed_claims = [c for c in all_claims if not c.get("visible") or (not c.get("decision") or c.get("decision", {}).get("decision") == "pending")]

    restricted_count = sum(1 for c in displayed_claims if not c.get("visible"))

    # Handle drawer opening
    drawer_html = None
    drawer_open = False
    if open_cite:
        source_id = open_cite.split(":")[0]
        src, lines = repo.get_lines_for_source(source_id, persona)
        if src and lines:
            drawer_open = True
            drawer_html = templates.get_template("drawer.html").render({
                "source": src,
                "lines": lines,
                "target_line_id": open_cite,
                "cite_count": 1,
            })

    # If this is an HTMX request for ledger only:
    if request.headers.get("HX-Request") == "true" and not request.headers.get("HX-Target") == "drawer":
        # Render the ledger-content fragment
        ledger_template = templates.from_string("""
{% import "_macros.html" as m %}
<div class="ledger" role="list">
  {% for item in claims %}
    {% if item.visible %}
      {{ m.claim_card(item, persona=role, is_selected=(open_line and open_line in item.cites|map(attribute='line_id')|list), index=loop.index0, open_line=open_line) }}
    {% else %}
      {{ m.redacted_claim(index=loop.index0) }}
    {% endif %}
  {% endfor %}
</div>
{% if restricted_count > 0 %}
<p class="meta" style="margin-top:var(--s3)">{{ restricted_count }} items are outside your clearance. Ask the approver, {{ case.approver or 'Divya Menon' }}.</p>
{% endif %}
""")
        return HTMLResponse(ledger_template.render({
            "claims": displayed_claims,
            "role": persona,
            "case": case,
            "restricted_count": restricted_count,
            "open_line": open_cite,
        }))

    return templates.TemplateResponse(
        request=request,
        name="claims.html",
        context={
            "request": request,
            "role": persona,
            "case": case,
            "counts": counts,
            "filter_counts": filter_counts,
            "claims": displayed_claims,
            "current_filter": type,
            "needs_review": bool(needs_review),
            "restricted_count": restricted_count,
            "drawer_open": drawer_open,
            "drawer_html": drawer_html,
            "open_line": open_cite,
            "active_tab": "claims",
        }
    )


@app.get("/case/{case_id}/claims/{cid}/source", response_class=HTMLResponse)
def claim_source_drawer(request: Request, case_id: int, cid: int, line: str | None = None):
    """Drawer partial for a claim cite. Returns 404 for hidden or invalid claim."""
    persona = get_persona_from_request(request)
    claim = repo.get_claim(case_id, cid, persona)
    if not claim:
        return not_found_response()

    target_line = line
    if not target_line and claim.get("cites"):
        target_line = claim["cites"][0]["line_id"]

    if not target_line:
        return not_found_response()

    source_id = target_line.split(":")[0]
    src, lines = repo.get_lines_for_source(source_id, persona)
    if not src or not lines:
        return not_found_response()

    # Count how many claims cite this source
    cite_count = len(claim.get("cites", []))

    return templates.TemplateResponse(
        request=request,
        name="drawer.html",
        context={
            "request": request,
            "source": src,
            "lines": lines,
            "target_line_id": target_line,
            "cite_count": cite_count,
        }
    )


@app.post("/case/{case_id}/claims/{cid}/decision")
def claim_decision(request: Request, case_id: int, cid: int, action: str):
    """Approve, edit, reject, or restore a claim (Approver only)."""
    persona = get_persona_from_request(request)
    if persona != "lead":
        raise HTTPException(status_code=403, detail="Only approver may record decisions")

    claim = repo.get_claim(case_id, cid, persona)
    if not claim or not claim.get("identity"):
        return not_found_response()

    identity = claim["identity"]
    if action in ("approve", "edit", "reject", "restore"):
        review.record_decision(case_id, identity, action, by="Divya Menon")

    # Re-fetch updated claim and render single claim card
    updated_claim = repo.get_claim(case_id, cid, persona)
    claim_macro_template = templates.from_string("""
{% import "_macros.html" as m %}
{{ m.claim_card(claim, persona='lead') }}
""")
    return HTMLResponse(claim_macro_template.render({"claim": updated_claim}))


# --- Stubs for other routes visited by test_design_rules ---

@app.get("/case/{case_id}/gaps", response_class=HTMLResponse)
def gaps_view(request: Request, case_id: int):
    persona = get_persona_from_request(request)
    case = repo.get_case(case_id, persona)
    counts = get_nav_counts(case_id, persona)
    return templates.TemplateResponse(request=request, name="gaps.html", context={"request": request, "role": persona, "case": case, "counts": counts, "active_tab": "gaps"})


@app.get("/case/{case_id}/conflicts", response_class=HTMLResponse)
def conflicts_view(request: Request, case_id: int):
    persona = get_persona_from_request(request)
    case = repo.get_case(case_id, persona)
    counts = get_nav_counts(case_id, persona)
    return templates.TemplateResponse(request=request, name="conflicts.html", context={"request": request, "role": persona, "case": case, "counts": counts, "active_tab": "conflicts"})


@app.get("/case/{case_id}/timeline", response_class=HTMLResponse)
def timeline_view(request: Request, case_id: int):
    persona = get_persona_from_request(request)
    case = repo.get_case(case_id, persona)
    counts = get_nav_counts(case_id, persona)
    return templates.TemplateResponse(request=request, name="timeline.html", context={"request": request, "role": persona, "case": case, "counts": counts, "active_tab": "timeline"})


@app.get("/case/{case_id}/handover", response_class=HTMLResponse)
def handover_view(request: Request, case_id: int):
    persona = get_persona_from_request(request)
    case = repo.get_case(case_id, persona)
    counts = get_nav_counts(case_id, persona)
    return templates.TemplateResponse(request=request, name="handover.html", context={"request": request, "role": persona, "case": case, "counts": counts, "active_tab": "handover"})


@app.get("/case/{case_id}/approvals", response_class=HTMLResponse)
def approvals_view(request: Request, case_id: int):
    persona = get_persona_from_request(request)
    case = repo.get_case(case_id, persona)
    counts = get_nav_counts(case_id, persona)
    return templates.TemplateResponse(request=request, name="approvals.html", context={"request": request, "role": persona, "case": case, "counts": counts, "active_tab": "approvals"})


@app.get("/trust", response_class=HTMLResponse)
def trust_view(request: Request):
    persona = get_persona_from_request(request)
    case = repo.get_case(1, persona)
    counts = get_nav_counts(1, persona) if case else {}
    dropped_claims = repo.get_dropped_claims(1, persona)
    return templates.TemplateResponse(
        request=request,
        name="trust.html",
        context={
            "request": request,
            "role": persona,
            "case": case,
            "counts": counts,
            "dropped_claims": dropped_claims,
            "active_tab": "trust",
        }
    )
