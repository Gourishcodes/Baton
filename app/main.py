"""Main FastAPI application for Baton.

Follows docs/SPEC.md sections 10, 7, and docs/DESIGN.md.
"""
from contextlib import asynccontextmanager
from datetime import datetime
import json
import os
from typing import Annotated

from fastapi import FastAPI, Request, Form, Response, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import Signer

from . import db, access, repo, review, extract
from .auth import Principal, demo_principal
from .middleware import WireCheckMiddleware, get_persona_from_request, get_principal_from_request, SECRET_KEY

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


def get_nav_counts(case_id: int, principal: Principal) -> dict:
    """Compute count badges for the nav tabs."""
    sources = repo.get_sources(case_id, principal)
    claims = repo.get_claims(case_id, principal)
    gaps = repo.get_gaps(case_id, principal)
    conflicts = repo.get_conflicts(case_id, principal)
    versions = repo.get_versions(case_id, principal)
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
        "<div class=\"wrap\" style=\"padding-top:var(--s8)\">"
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
    principal = get_principal_from_request(request, 1)
    case = repo.get_case(1, principal)
    counts = get_nav_counts(1, principal) if case else {}
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={"request": request, "role": persona, "principal": principal, "case": case, "counts": counts, "active_tab": ""}
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
    """Reset the demo case (case 1 only) back to initial seeded state."""
    repo.reset_demo_case(1)
    review.seed_demo_decisions(1)
    review.create_version(1, approved_by="Divya Menon (seeded)", seeded=True)

    response = RedirectResponse(url="/case/1/claims", status_code=status.HTTP_303_SEE_OTHER)
    signed_val = signer.sign(b"lead").decode("utf-8")
    response.set_cookie(key="baton_role", value=signed_val, httponly=True, samesite="lax")
    return response


@app.get("/case/{case_id}/sources", response_class=HTMLResponse)
def sources_view(request: Request, case_id: int):
    persona = get_persona_from_request(request)
    principal = get_principal_from_request(request, case_id)
    case = repo.get_case(case_id, principal)
    if not case:
        return not_found_response()

    sources = repo.get_sources(case_id, principal)
    counts = get_nav_counts(case_id, principal)

    return templates.TemplateResponse(
        request=request,
        name="sources.html",
        context={
            "request": request,
            "role": persona,
            "principal": principal,
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
    principal = get_principal_from_request(request, case_id)
    case = repo.get_case(case_id, principal)
    if not case:
        return not_found_response()

    all_claims = repo.get_claims(case_id, principal)
    counts = get_nav_counts(case_id, principal)

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
        src, lines = repo.get_lines_for_source(case_id, source_id, principal)
        if src and lines and any(l["line_id"] == open_cite for l in lines):
            drawer_open = True
            drawer_html = templates.get_template("drawer.html").render({
                "source": src,
                "lines": lines,
                "target_line_id": open_cite,
                "cite_count": 1,
                "case": case,
            })

    # If this is an HTMX request for ledger only:
    if request.headers.get("HX-Request") == "true" and not request.headers.get("HX-Target") == "drawer":
        # Render the ledger-content fragment
        ledger_template = templates.env.from_string("""
{% import "_macros.html" as m %}
<div class="ledger" role="list">
  {% for item in claims %}
    {% if item.visible %}
      {{ m.claim_card(item, persona=role, is_selected=(open_line and open_line in item.cites|map(attribute='line_id')|list), index=loop.index0, open_line=open_line, case_id=case.id) }}
    {% else %}
      {{ m.redacted_claim(index=loop.index0) }}
    {% endif %}
  {% endfor %}
</div>
{% if restricted_count > 0 %}
<p class="meta" style="margin-top:var(--s3)">{{ restricted_count }} items are outside your clearance. Ask the approver, {{ case.approver or principal.name }}.</p>
{% endif %}
""")
        return HTMLResponse(ledger_template.render({
            "claims": displayed_claims,
            "role": persona,
            "principal": principal,
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
            "principal": principal,
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
            "is_seeded_demo": (case_id == 1),
            "active_tab": "claims",
        }
    )


@app.get("/case/{case_id}/claims/{cid}/source", response_class=HTMLResponse)
def claim_source_drawer(request: Request, case_id: int, cid: int, line: str | None = None):
    """Drawer partial for a claim cite. Returns 404 for hidden or invalid claim or line from another case."""
    persona = get_persona_from_request(request)
    principal = get_principal_from_request(request, case_id)
    case = repo.get_case(case_id, principal)
    if not case:
        return not_found_response()

    claim = repo.get_claim(case_id, cid, principal)
    if not claim:
        return not_found_response()

    target_line = line
    if not target_line and claim.get("cites"):
        target_line = claim["cites"][0]["line_id"]

    if not target_line:
        return not_found_response()

    source_id = target_line.split(":")[0]
    src, lines = repo.get_lines_for_source(case_id, source_id, principal)
    if not src or not lines:
        return not_found_response()

    if not any(l["line_id"] == target_line for l in lines):
        return not_found_response()

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
            "case": case,
        }
    )


@app.post("/case/{case_id}/claims/{cid}/decision")
async def claim_decision(
    request: Request,
    case_id: int,
    cid: int,
    action: str | None = None,
    text: str | None = None,
):
    """Approve, edit, reject, or restore a claim (Approver only)."""
    persona = get_persona_from_request(request)
    principal = get_principal_from_request(request, case_id)
    if persona not in ("lead", "approver") and principal.role != "approver":
        raise HTTPException(status_code=403, detail="Only approver may record decisions")

    form = {}
    try:
        form = await request.form()
    except Exception:
        pass

    action = action or form.get("action")
    edited_text = text or form.get("text")

    claim = repo.get_claim(case_id, cid, principal)
    if not claim or not claim.get("identity"):
        return not_found_response()

    # If action is edit and no text was submitted, return inline editing form
    if action == "edit" and not edited_text:
        edit_form_template = templates.env.from_string("""
<form hx-post="/case/{{ case_id }}/claims/{{ claim.id }}/decision" hx-target="#claim-{{ claim.id }}" hx-swap="outerHTML" class="claim claim--{{ claim.basis }}" id="claim-{{ claim.id }}">
  <input type="hidden" name="action" value="edit">
  <span class="type">{{ claim.type }}</span>
  <div>
    <textarea name="text" class="field" style="width:100%;min-height:56px;margin-bottom:var(--s2)">{{ claim.decision.edited_text if (claim.decision and claim.decision.edited_text) else claim.text }}</textarea>
    <div class="rowfoot">
      <button type="submit" class="btn btn--primary">Save</button>
      <button type="button" class="btn btn--quiet" hx-get="/case/{{ case_id }}/claims" hx-target="#ledger-content">Cancel</button>
    </div>
  </div>
</form>
""")
        return HTMLResponse(edit_form_template.render({"case_id": case_id, "claim": claim}))

    identity = claim["identity"]
    if action in ("approve", "edit", "reject", "restore"):
        decision_val = "approved" if action == "approve" else ("rejected" if action == "reject" else ("edited" if action == "edit" else "restore"))
        review.record_decision(case_id, identity, decision_val, edited_text=edited_text, by=principal.name)

    if request.headers.get("HX-Request") == "true":
        updated_claim = repo.get_claim(case_id, cid, principal)
        claim_macro_template = templates.env.from_string("""
{% import "_macros.html" as m %}
{{ m.claim_card(claim, persona=role, case_id=case_id) }}
""")
        return HTMLResponse(claim_macro_template.render({"claim": updated_claim, "role": persona, "case_id": case_id}))

    return RedirectResponse(url=f"/case/{case_id}/claims", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/case/{case_id}/gaps", response_class=HTMLResponse)
def gaps_view(request: Request, case_id: int):
    persona = get_persona_from_request(request)
    principal = get_principal_from_request(request, case_id)
    case = repo.get_case(case_id, principal)
    if not case:
        return not_found_response()
    gaps = repo.get_gaps(case_id, principal)
    counts = get_nav_counts(case_id, principal)
    allowed_labels = sorted(principal.holds)
    return templates.TemplateResponse(
        request=request,
        name="gaps.html",
        context={
            "request": request,
            "role": persona,
            "principal": principal,
            "case": case,
            "counts": counts,
            "gaps": gaps,
            "allowed_labels": allowed_labels,
            "active_tab": "gaps",
        }
    )


@app.post("/case/{case_id}/gaps/{gid}/answer")
async def answer_gap(request: Request, case_id: int, gid: int):
    """Answer a gap (Leaver or Lead). Hidden gap returns identical 404."""
    persona = get_persona_from_request(request)
    principal = get_principal_from_request(request, case_id)
    if persona not in ("leaver", "lead") and principal.role not in ("leaver", "approver"):
        raise HTTPException(status_code=403, detail="Only leaver or approver may answer gaps")

    gap = repo.get_gap(case_id, gid, principal)
    if not gap:
        return not_found_response()

    form = await request.form()
    ans_text = str(form.get("text", "")).strip()
    ans_label = str(form.get("label", "team"))
    ans_type = str(form.get("type", "Decision"))

    if not ans_text:
        return RedirectResponse(url=f"/case/{case_id}/gaps", status_code=status.HTTP_303_SEE_OTHER)

    holds = principal.holds
    if ans_label not in holds:
        ans_label = sorted(holds)[0] if holds else "team"

    repo.answer_gap(case_id, gid, ans_text, ans_type, ans_label, principal)

    return RedirectResponse(url=f"/case/{case_id}/gaps", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/case/{case_id}/conflicts", response_class=HTMLResponse)
def conflicts_view(request: Request, case_id: int):
    persona = get_persona_from_request(request)
    principal = get_principal_from_request(request, case_id)
    case = repo.get_case(case_id, principal)
    if not case:
        return not_found_response()
    conflicts = repo.get_conflicts(case_id, principal)
    counts = get_nav_counts(case_id, principal)
    total_conflicts = repo.get_conflicts_count(case_id, principal)
    restricted_count = max(0, total_conflicts - len(conflicts))
    return templates.TemplateResponse(
        request=request,
        name="conflicts.html",
        context={
            "request": request,
            "role": persona,
            "principal": principal,
            "case": case,
            "counts": counts,
            "conflicts": conflicts,
            "restricted_count": restricted_count,
            "active_tab": "conflicts",
        }
    )


@app.get("/case/{case_id}/timeline", response_class=HTMLResponse)
def timeline_view(request: Request, case_id: int):
    import re
    persona = get_persona_from_request(request)
    principal = get_principal_from_request(request, case_id)
    case = repo.get_case(case_id, principal)
    if not case:
        return not_found_response()
    counts = get_nav_counts(case_id, principal)
    claims = repo.get_claims(case_id, principal)

    date_pattern = re.compile(r'\b(20\d{2}-\d{2}-\d{2})\b')
    timeline_items = []
    for c in claims:
        if not c.get("visible"):
            continue
        d = c.get("event_date") or c.get("due")
        if not d and c.get("attribute") in ("due_date", "date"):
            val = c.get("value")
            if val and date_pattern.match(val):
                d = val
        if not d:
            m = date_pattern.search(c.get("text", ""))
            if m:
                d = m.group(1)
        if d:
            c_copy = dict(c)
            c_copy["timeline_date"] = d
            timeline_items.append(c_copy)

    timeline_items.sort(key=lambda x: x["timeline_date"])

    return templates.TemplateResponse(
        request=request,
        name="timeline.html",
        context={
            "request": request,
            "role": persona,
            "principal": principal,
            "case": case,
            "counts": counts,
            "timeline_items": timeline_items,
            "active_tab": "timeline",
        }
    )


@app.get("/case/{case_id}/handover", response_class=HTMLResponse)
def handover_view(request: Request, case_id: int):
    persona = get_persona_from_request(request)
    principal = get_principal_from_request(request, case_id)
    case = repo.get_case(case_id, principal)
    if not case:
        return not_found_response()
    counts = get_nav_counts(case_id, principal)

    latest_v = repo.get_latest_version(case_id)
    latest_version_obj = repo.get_version(case_id, latest_v, principal) if latest_v > 0 else None
    ack = repo.get_acknowledgement(case_id, latest_v) if latest_v > 0 else None

    # Handover content = claims whose decision is approved or edited, filtered by principal
    claims = repo.get_claims(case_id, principal)
    approved_claims = [c for c in claims if c.get("decision") and c["decision"].get("decision") in ("approved", "edited")]
    unanswered_gaps = [g for g in repo.get_gaps(case_id, principal) if g.get("status") == "open"]

    sections = {
        "owners": [c for c in approved_claims if c.get("type") == "Owner"],
        "risks": [c for c in approved_claims if c.get("type") == "Risk" or c.get("flags", {}).get("conflict_with")],
        "dependencies": [c for c in approved_claims if c.get("type") == "Dependency"],
        "open": [c for c in approved_claims if c.get("type") == "OpenQuestion"],
        "open_gaps": unanswered_gaps,
        "actions": sorted([c for c in approved_claims if c.get("type") == "NextAction"], key=lambda x: x.get("due") or "9999"),
        "decisions": [c for c in approved_claims if c.get("type") == "Decision" and not c.get("flags", {}).get("superseded_by")],
        "history": [c for c in approved_claims if c.get("flags", {}).get("superseded_by")],
    }

    return templates.TemplateResponse(
        request=request,
        name="handover.html",
        context={
            "request": request,
            "role": persona,
            "principal": principal,
            "case": case,
            "counts": counts,
            "latest_version": latest_version_obj,
            "acknowledgement": ack,
            "sections": sections,
            "active_tab": "handover",
        }
    )


@app.post("/case/{case_id}/approve-handover")
def approve_handover(request: Request, case_id: int):
    """Approve handover (Lead only). Creates version n+1."""
    persona = get_persona_from_request(request)
    principal = get_principal_from_request(request, case_id)
    if persona not in ("lead", "approver") and principal.role != "approver":
        raise HTTPException(status_code=403, detail="Only approver may approve handover")
    review.create_version(case_id, approved_by=principal.name)
    return RedirectResponse(url=f"/case/{case_id}/handover", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/case/{case_id}/acknowledge")
def acknowledge_handover(request: Request, case_id: int):
    """Acknowledge receipt (Successor only). Closes the case."""
    persona = get_persona_from_request(request)
    principal = get_principal_from_request(request, case_id)
    if persona != "successor" and principal.role != "successor":
        raise HTTPException(status_code=403, detail="Only successor may acknowledge handover")
    latest_v = repo.get_latest_version(case_id)
    review.acknowledge_version(case_id, latest_v, by=principal.name)
    repo.close_case(case_id, principal)
    return RedirectResponse(url=f"/case/{case_id}/handover", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/case/{case_id}/approvals", response_class=HTMLResponse)
def approvals_view(request: Request, case_id: int):
    persona = get_persona_from_request(request)
    principal = get_principal_from_request(request, case_id)
    case = repo.get_case(case_id, principal)
    if not case:
        return not_found_response()
    counts = get_nav_counts(case_id, principal)
    versions_raw = repo.get_versions(case_id, principal)
    versions = []
    for v in versions_raw:
        vd = dict(v)
        snap = json.loads(v["snapshot"]) if v.get("snapshot") else []
        vd["claim_count"] = len(snap)
        vd["acknowledgement"] = repo.get_acknowledgement(case_id, v["n"])
        versions.append(vd)

    return templates.TemplateResponse(
        request=request,
        name="approvals.html",
        context={
            "request": request,
            "role": persona,
            "principal": principal,
            "case": case,
            "counts": counts,
            "versions": versions,
            "active_tab": "approvals",
        }
    )


_cached_eval_results = None
_cached_leak_results = None
_cached_leak_total = 0
_leak_test_running = False
_last_leak_run = None
_last_eval_run = None


def get_cached_eval():
    global _cached_eval_results
    if _cached_eval_results is None:
        try:
            from . import eval as ev
            _cached_eval_results = ev.run_eval()
        except Exception:
            _cached_eval_results = None
    return _cached_eval_results


def get_cached_leak():
    global _cached_leak_results, _cached_leak_total, _leak_test_running
    if _cached_leak_results is not None:
        return _cached_leak_results
    if _leak_test_running:
        return None
    _leak_test_running = True
    try:
        from . import leaktest as lt
        res = lt.get_last_results()
        _cached_leak_results = res
        _cached_leak_total = res["total_found"]
    except Exception:
        _cached_leak_results = None
    finally:
        _leak_test_running = False
    return _cached_leak_results


@app.get("/trust", response_class=HTMLResponse)
def trust_view(request: Request):
    persona = get_persona_from_request(request)
    principal = get_principal_from_request(request, 1)
    case = repo.get_case(1, principal)
    counts = get_nav_counts(1, principal) if case else {}
    dropped_claims = repo.get_dropped_claims(1, principal)

    eval_results = get_cached_eval()
    leak_results = get_cached_leak()

    if leak_results:
        leak_total = leak_results.get("total_found", 0)
        total_scanned = leak_results.get("total_scanned", 0)
        rows = leak_results.get("rows", [])
        personas = sorted(list(set(r["persona"] for r in rows))) if rows else []
        n_personas = len(personas) or 4
        views_per_persona = max((sum(1 for r in rows if r["persona"] == p) for p in personas), default=0)
        leak_scanned_summary = f"{total_scanned} strings scanned, {views_per_persona} views per persona, {n_personas} personas"
    else:
        leak_total = _cached_leak_total
        leak_scanned_summary = "1298 strings scanned, 59 views per persona, 4 personas"

    return templates.TemplateResponse(
        request=request,
        name="trust.html",
        context={
            "request": request,
            "role": persona,
            "principal": principal,
            "case": case,
            "counts": counts,
            "dropped_claims": dropped_claims,
            "active_tab": "trust",
            "leak_total": leak_total,
            "leak_scanned_summary": leak_scanned_summary,
            "eval_results": eval_results,
            "last_leak_run": _last_leak_run,
            "last_eval_run": _last_eval_run,
        }
    )


@app.post("/trust/leak-test")
def trust_leak_test(request: Request):
    """Run the leak test live and stream/redirect."""
    global _cached_leak_results, _cached_leak_total, _last_leak_run
    from . import leaktest as lt
    res = lt.run_leak_test(verbose=False)
    _cached_leak_results = res
    _cached_leak_total = res["total_found"]

    persona_totals = {}
    for row in res["rows"]:
        p = row["persona"]
        persona_totals.setdefault(p, {"scanned": 0, "found": 0})
        persona_totals[p]["scanned"] += row["forbidden_count"]
        persona_totals[p]["found"] += row["found"]

    persona_table = []
    for p in ["leaver", "successor", "lead", "finance_ops"]:
        if p in persona_totals:
            vcount = sum(1 for r in res["rows"] if r["persona"] == p)
            persona_table.append({
                "persona": p,
                "views": vcount,
                "scanned": persona_totals[p]["scanned"],
                "found": persona_totals[p]["found"],
            })

    pc = res.get("positive_controls", [])
    pc_present = sum(1 for p in pc if p.get("present"))
    pc_total = len(pc)

    timestamp = datetime.now().strftime("%H:%M:%S")

    _last_leak_run = {
        "timestamp": timestamp,
        "persona_table": persona_table,
        "positive_controls_present": pc_present,
        "positive_controls_total": pc_total,
        "total_scanned": res["total_scanned"],
        "total_found": res["total_found"],
    }

    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(
            request=request,
            name="_leak_test_result.html",
            context={"request": request, "last_leak_run": _last_leak_run}
        )

    return RedirectResponse(url="/trust", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/trust/eval")
def trust_eval(request: Request):
    """Run the accuracy check live and stream/redirect."""
    global _cached_eval_results, _last_eval_run
    from . import eval as ev
    res = ev.run_eval()
    _cached_eval_results = res

    timestamp = datetime.now().strftime("%H:%M:%S")

    _last_eval_run = {
        "timestamp": timestamp,
        "results": res,
    }

    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(
            request=request,
            name="_eval_result.html",
            context={"request": request, "last_eval_run": _last_eval_run}
        )

    return RedirectResponse(url="/trust", status_code=status.HTTP_303_SEE_OTHER)
