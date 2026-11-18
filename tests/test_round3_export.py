"""Tests for Round 3 permission-safe export, single handover builder, and wire check extension.

Implements requirements from ROUND3_SPEC sections 2, 3 and Prompt 3:
1. build_handover(case_id, principal) as single source for handover content.
2. GET /case/{id}/handover.md and GET /case/{id}/handover/print headers, filenames, 404s.
3. Content rules: sections, subset guarantee, no label markup, line ID citations, footer.
4. Slug generation with quotes, slashes, and CRLF.
5. WireCheckMiddleware blocking Markdown responses with planted canaries.
6. Access log recording every export with format and version.
7. Equivalence test between Markdown export and handover page.
8. Markdown and HTML escaping for XSS payloads.
"""
from datetime import datetime, timezone
import json
import os
import re
import sys
import tempfile
import pytest
from starlette.testclient import TestClient

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import db, repo, extract, review, main
from app.auth import (
    seed_demo_accounts,
    create_session,
    create_user,
    demo_principal,
    login_as,
    DEMO_PASSWORD,
)
from app.repo import build_handover, slugify, escape_markdown, render_handover_markdown


@pytest.fixture(autouse=True)
def isolated_db():
    """Run each test with an isolated database."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    old_path = db.get_db_path() if db._db_path else None
    db.set_db_path(tmp.name)
    yield tmp.name
    db.close()
    try:
        os.unlink(tmp.name)
    except Exception:
        pass
    if old_path:
        db.set_db_path(old_path)
    else:
        db.reset()


def _seed_case_1():
    db.load_demo_case()
    seed_demo_accounts()
    extract.extract_for_case(1, mode="cache_only")
    review.seed_demo_decisions(1)
    review.create_version(1, approved_by="Divya Menon (seeded)", seeded=True)


def test_build_handover_returns_single_source_structure():
    """build_handover(case_id, principal) returns all sections and metadata."""
    _seed_case_1()
    p_lead = demo_principal("lead", case_id=1)
    data = build_handover(1, p_lead)

    assert "case" in data
    assert "latest_version" in data
    assert "version" in data
    assert "acknowledgement" in data
    assert "sections" in data

    sections = data["sections"]
    for sec_name in ["owners", "risks", "dependencies", "open", "open_gaps", "actions", "decisions", "history"]:
        assert sec_name in sections

    assert data["version"] == 1
    assert data["case"]["name"] == "Dispatch notifications"


def test_export_routes_headers_and_status():
    """GET /case/{id}/handover.md and GET /case/{id}/handover/print return correct headers and content types."""
    _seed_case_1()
    client = TestClient(main.app)
    login_as(client, "lead")

    # 1. Markdown export
    resp_md = client.get("/case/1/handover.md")
    assert resp_md.status_code == 200
    assert "text/markdown" in resp_md.headers.get("content-type", "")
    assert "charset=utf-8" in resp_md.headers.get("content-type", "")
    assert resp_md.headers.get("x-content-type-options") == "nosniff"
    disp = resp_md.headers.get("content-disposition", "")
    assert "attachment;" in disp
    assert 'filename="handover-dispatch-notifications-v1.md"' in disp

    # 2. Print page
    resp_print = client.get("/case/1/handover/print")
    assert resp_print.status_code == 200
    assert "text/html" in resp_print.headers.get("content-type", "")
    assert "Print or save as PDF" in resp_print.text
    assert "window.print()" in resp_print.text


def test_export_unapproved_case_returns_standard_404():
    """Both export routes return standard 404 when no approved version exists."""
    db.load_demo_case()
    seed_demo_accounts()
    # No extraction, decisions, or version created
    client = TestClient(main.app)
    login_as(client, "lead")

    standard_404 = main.not_found_response().body.decode("utf-8")

    resp_md = client.get("/case/1/handover.md")
    assert resp_md.status_code == 404
    assert resp_md.text == standard_404

    resp_print = client.get("/case/1/handover/print")
    assert resp_print.status_code == 404
    assert resp_print.text == standard_404


def test_export_non_member_returns_standard_404():
    """A user who is not a member of the case receives standard 404 on exports."""
    _seed_case_1()
    client = TestClient(main.app)

    # Create real user who has no membership in case 1
    uid = create_user("outsider@example.com", "Outsider", "longpassword123")
    token = create_session(uid)
    client.cookies["baton_session"] = token

    standard_404 = main.not_found_response().body.decode("utf-8")

    resp_md = client.get("/case/1/handover.md")
    assert resp_md.status_code == 404
    assert resp_md.text == standard_404

    resp_print = client.get("/case/1/handover/print")
    assert resp_print.status_code == 404
    assert resp_print.text == standard_404


def test_different_roles_get_different_files_and_successor_is_strict_subset():
    """Successor's export is a strict subset of the approver's export."""
    _seed_case_1()
    client = TestClient(main.app)

    # Lead (approver)
    login_as(client, "lead")
    lead_md = client.get("/case/1/handover.md").text

    # Successor
    login_as(client, "successor")
    succ_md = client.get("/case/1/handover.md").text

    assert lead_md != succ_md

    # Extract claims from Markdown using the claim comments
    lead_claim_ids = set(int(x) for x in re.findall(r'<!-- claim:(\d+) -->', lead_md))
    succ_claim_ids = set(int(x) for x in re.findall(r'<!-- claim:(\d+) -->', succ_md))

    assert len(succ_claim_ids) > 0
    assert len(lead_claim_ids) > len(succ_claim_ids)
    assert succ_claim_ids.issubset(lead_claim_ids)
    assert not lead_claim_ids.issubset(succ_claim_ids)


def test_no_export_contains_restricted_or_label_markup():
    """No export contains 'Restricted', 'outside your clearance', or label names in label markup."""
    _seed_case_1()
    client = TestClient(main.app)

    personas = ["lead", "leaver", "successor", "finance_ops"]
    for p in personas:
        login_as(client, p)
        md_text = client.get("/case/1/handover.md").text
        print_text = client.get("/case/1/handover/print").text

        for text, name in [(md_text, "Markdown"), (print_text, "Print")]:
            assert "Restricted" not in text, f"{p} {name} contains 'Restricted'"
            assert "outside your clearance" not in text.lower(), f"{p} {name} contains 'outside your clearance'"

            # Tag markup must be absent
            assert 'class="tag' not in text, f"{p} {name} contains tag element"
            assert 'tag--restricted' not in text, f"{p} {name} contains tag--restricted"

            # Bracketed labels like [finance], [hr], [team] must be absent
            assert not re.search(r'\[(finance|hr|team)\]', text, re.IGNORECASE), f"{p} {name} contains bracketed label"

            # Check that "finance" or "hr" do not appear as label names (allow words like through, three, etc.)
            # Check for standalone label badges or tags: e.g. "Label: finance"
            assert not re.search(r'(?i)\blabel:\s*(finance|hr)\b', text), f"{p} {name} contains label attribute"


def test_equivalence_claim_ids_markdown_equals_handover_page():
    """For every account, claim IDs in Markdown export equal claim IDs on handover page."""
    _seed_case_1()
    client = TestClient(main.app)

    for persona in ["lead", "leaver", "successor", "finance_ops"]:
        login_as(client, persona)

        ho_resp = client.get("/case/1/handover")
        assert ho_resp.status_code == 200
        ho_ids = set(int(x) for x in re.findall(r'<!-- claim:(\d+) -->', ho_resp.text))
        if not ho_ids:
            ho_ids = set(int(x) for x in re.findall(r'data-claim-id="(\d+)"', ho_resp.text))

        md_resp = client.get("/case/1/handover.md")
        assert md_resp.status_code == 200
        md_ids = set(int(x) for x in re.findall(r'<!-- claim:(\d+) -->', md_resp.text))

        assert ho_ids == md_ids, f"Mismatch for {persona}: {len(ho_ids)} on page vs {len(md_ids)} in markdown"


def test_slug_generation_with_quotes_slashes_and_crlf():
    """Slugify handles case names containing quotes, slashes, and CRLF producing only lowercase letters, digits, and hyphens."""
    dirty_name = 'Project "Alpha" / Beta\r\nTest & Engine 100%'
    slug = slugify(dirty_name)
    assert re.match(r'^[a-z0-9]+(-[a-z0-9]+)*$', slug), f"Invalid slug: {slug}"
    assert '"' not in slug
    assert '/' not in slug
    assert '\r' not in slug
    assert '\n' not in slug
    assert slug == "project-alpha-beta-test-engine-100"

    # Test via route with custom case name
    _seed_case_1()
    conn = db.get_conn()
    user = conn.execute("SELECT id FROM users WHERE email = 'divya@example.com'").fetchone()
    uid = user["id"]
    cur = conn.execute(
        "INSERT INTO cases (name, status, created_at) VALUES (?, 'open', ?)",
        ('Case "Special" / 2026\r\nHandover', db.now_iso())
    )
    new_cid = cur.lastrowid
    # Add membership for lead
    conn.execute(
        "INSERT INTO memberships (case_id, user_id, role, is_owner, labels, added_at) VALUES (?, ?, 'approver', 1, '[\"team\",\"finance\",\"hr\"]', ?)",
        (new_cid, uid, db.now_iso())
    )
    # Create version
    conn.execute(
        "INSERT INTO versions (case_id, n, approved_by, approved_at, snapshot, seeded) VALUES (?, 1, 'Divya Menon', ?, '[]', 1)",
        (new_cid, db.now_iso())
    )
    conn.commit()

    client = TestClient(main.app)
    login_as(client, "lead")
    resp = client.get(f"/case/{new_cid}/handover.md")
    assert resp.status_code == 200
    disp = resp.headers.get("content-disposition", "")
    assert 'filename="handover-case-special-2026-handover-v1.md"' in disp


def test_wirecheck_middleware_blocks_markdown_with_planted_canary():
    """WireCheckMiddleware fails closed (500) if a Markdown response contains a forbidden canary."""
    _seed_case_1()
    client = TestClient(main.app)

    # Successor cannot see finance canary PW-CONTRACT-7731
    # We will simulate a leak by inserting an approved claim containing PW-CONTRACT-7731 but wrongly labelled "team"
    conn = db.get_conn()
    cur = conn.execute(
        """INSERT INTO claims (case_id, run_id, type, text, basis, labels, flags, identity)
           VALUES (1, 1, 'Decision', 'Leak test PW-CONTRACT-7731 should be blocked', 'sourced', '["team"]', '{}', 'leak_test:1')"""
    )
    leak_cid = cur.lastrowid
    conn.execute(
        "INSERT INTO decisions (identity, case_id, decision, by, at, seeded) VALUES ('leak_test:1', 1, 'approved', 'Divya Menon', ?, 1)",
        (db.now_iso(),)
    )
    conn.commit()

    login_as(client, "successor")
    resp = client.get("/case/1/handover.md")
    assert resp.status_code == 500
    assert "Blocked: a restricted string reached this response" in resp.text

    # Cleanup
    conn.execute("DELETE FROM claims WHERE id = ?", (leak_cid,))
    conn.execute("DELETE FROM decisions WHERE identity = 'leak_test:1'")
    conn.commit()


def test_export_is_logged_in_access_log():
    """Every export is logged in access_log with format and version."""
    _seed_case_1()
    client = TestClient(main.app)
    login_as(client, "successor")

    conn = db.get_conn()
    conn.execute("DELETE FROM access_log")
    conn.commit()

    # 1. Export markdown
    client.get("/case/1/handover.md")
    # 2. Export print
    client.get("/case/1/handover/print")

    logs = conn.execute("SELECT * FROM access_log WHERE case_id = 1 ORDER BY id ASC").fetchall()
    assert len(logs) == 2

    log_md = dict(logs[0])
    assert log_md["action"] == "export"
    assert "/case/1/handover.md" in log_md["route"]
    items_md = json.loads(log_md["items"])
    assert items_md["format"] == "markdown"
    assert items_md["version"] == 1

    log_print = dict(logs[1])
    assert log_print["action"] == "export"
    assert "/case/1/handover/print" in log_print["route"]
    items_print = json.loads(log_print["items"])
    assert items_print["format"] == "print"
    assert items_print["version"] == 1


def test_escaping_markdown_and_html():
    """Claim text containing <script> and [x](javascript:alert(1)) is escaped in Markdown and Print."""
    _seed_case_1()
    conn = db.get_conn()

    xss_text = "Check <script>alert('xss')</script> and [x](javascript:alert(1)) end"
    cur = conn.execute(
        """INSERT INTO claims (case_id, run_id, type, text, basis, labels, flags, identity)
           VALUES (1, 1, 'Decision', ?, 'sourced', '["team"]', '{}', 'xss_payload:1')""",
        (xss_text,)
    )
    xss_cid = cur.lastrowid
    conn.execute(
        "INSERT INTO decisions (identity, case_id, decision, by, at, seeded) VALUES ('xss_payload:1', 1, 'approved', 'Divya Menon', ?, 1)",
        (db.now_iso(),)
    )
    conn.commit()

    client = TestClient(main.app)
    login_as(client, "lead")

    # 1. Markdown export
    resp_md = client.get("/case/1/handover.md")
    assert resp_md.status_code == 200
    assert "<script>" not in resp_md.text
    assert "\\<script\\>" in resp_md.text or "&lt;script&gt;" in resp_md.text
    assert "[x](javascript:alert(1))" not in resp_md.text
    assert r"\[x\]\(javascript:alert\(1\)\)" in resp_md.text

    # 2. Print export
    resp_print = client.get("/case/1/handover/print")
    assert resp_print.status_code == 200
    assert "<script>" not in resp_print.text
    assert "&lt;script&gt;" in resp_print.text
