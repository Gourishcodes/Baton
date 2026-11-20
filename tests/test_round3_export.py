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

    # Extract claims from Markdown using bullet lines
    lead_bullets = set(l.strip() for l in lead_md.splitlines() if l.strip().startswith("- "))
    succ_bullets = set(l.strip() for l in succ_md.splitlines() if l.strip().startswith("- "))

    assert len(succ_bullets) > 0
    assert len(lead_bullets) > len(succ_bullets)
    assert succ_bullets.issubset(lead_bullets)
    assert not lead_bullets.issubset(succ_bullets)


def test_no_internal_ids_in_exports():
    """No internal id (claim, gap, user or case) may appear in any export."""
    _seed_case_1()
    client = TestClient(main.app)
    for p in ["lead", "successor", "finance_ops", "leaver"]:
        login_as(client, p)
        md = client.get("/case/1/handover.md").text
        pr = client.get("/case/1/handover/print").text
        for text, fmt in [(md, "markdown"), (pr, "print")]:
            assert "<!-- claim:" not in text, f"Found claim comment in {p} {fmt}"
            assert "data-claim-id" not in text, f"Found data-claim-id in {p} {fmt}"
            assert "<!-- gap:" not in text, f"Found gap comment in {p} {fmt}"
            assert not re.search(r'\b(claim_id|gap_id|user_id|case_id)\b', text), f"Found internal id key in {p} {fmt}"


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
    """For every account, ordered claim text plus citation ids of each section
    between the handover page and the .md match, with no IDs needed."""
    _seed_case_1()
    client = TestClient(main.app)
    from app.leaktest import _parse_html_sections, _parse_md_sections

    canonical_sections = [
        "Who owns what", "What is at risk", "What depends on what",
        "What is open", "What to do first", "Decisions and why", "History"
    ]

    for persona in ["lead", "leaver", "successor", "finance_ops"]:
        login_as(client, persona)

        ho_resp = client.get("/case/1/handover")
        assert ho_resp.status_code == 200
        ho_secs = _parse_html_sections(ho_resp.text)

        md_resp = client.get("/case/1/handover.md")
        assert md_resp.status_code == 200
        md_secs = _parse_md_sections(md_resp.text)

        for sec in canonical_sections:
            assert ho_secs.get(sec, []) == md_secs.get(sec, []), (
                f"Mismatch in section '{sec}' for {persona}:\nPage: {ho_secs.get(sec, [])}\nMD: {md_secs.get(sec, [])}"
            )


def test_demo_data_each_approved_claim_id_appears_exactly_once_per_export():
    """On the demo data, each approved claim id appears exactly once across build_handover sections,
    and superseded claims appear only under History, never in any other section."""
    _seed_case_1()

    for persona in ["lead", "successor"]:
        p = demo_principal(persona, case_id=1)
        data = build_handover(1, p)
        sections = data["sections"]

        all_cids = []
        for sec_name, items in sections.items():
            if sec_name == "open_gaps":
                continue
            for item in items:
                cid = item.get("id")
                if cid is not None:
                    all_cids.append(cid)

        # Assert no duplicates across sections
        assert len(all_cids) == len(set(all_cids)), f"Duplicate claim IDs in {persona} handover: {all_cids}"

        # Superseded claims (e.g. claim 2) must appear ONLY in history
        history_cids = [item.get("id") for item in sections.get("history", [])]
        non_history_cids = []
        for sec_name, items in sections.items():
            if sec_name not in ("history", "open_gaps"):
                non_history_cids.extend([item.get("id") for item in items])

        # Claim 2 is superseded
        assert 2 in history_cids, f"Claim 2 missing from history for {persona}"
        assert 2 not in non_history_cids, f"Claim 2 found in non-history sections for {persona}"


def test_markdown_escaping_plain_parentheses_unaltered():
    """Escape only characters that change Markdown meaning; do not escape plain parentheses."""
    _seed_case_1()
    client = TestClient(main.app)
    login_as(client, "lead")

    md_resp = client.get("/case/1/handover.md")
    assert md_resp.status_code == 200
    # Must NOT have escaped parentheses \(notify/ package\)
    assert r"\(notify/ package\)" not in md_resp.text
    assert "(notify/ package)" in md_resp.text


def test_finance_ops_export_positive_controls():
    """finance_ops exports contain the finance canaries that appear in approved claims."""
    _seed_case_1()
    client = TestClient(main.app)
    login_as(client, "finance_ops")

    md_resp = client.get("/case/1/handover.md")
    assert md_resp.status_code == 200
    print_resp = client.get("/case/1/handover/print")
    assert print_resp.status_code == 200

    approved_finance_canaries = ["₹3,18,400", "7.4 months", "₹24,750", "₹19,200"]
    for canary in approved_finance_canaries:
        assert canary in md_resp.text, f"Canary {canary} missing from finance_ops .md"
        assert canary in print_resp.text, f"Canary {canary} missing from finance_ops print"


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
    assert items_md["format"] in ("md", "markdown")
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
    assert r"\[x\](javascript:alert(1))" in resp_md.text

    # 2. Print export
    resp_print = client.get("/case/1/handover/print")
    assert resp_print.status_code == 200
    assert "<script>" not in resp_print.text
    assert "&lt;script&gt;" in resp_print.text


def test_claim_in_open_conflict_has_disagreement_note():
    """For a claim in an open conflict, build_handover adds the note:
    'Disagrees with the claim at <first cite of the other claim>. Not yet decided by the approver.'
    using only line id, same on screen, .md and print page."""
    _seed_case_1()
    client = TestClient(main.app)
    login_as(client, "lead")

    # Find the conflicting decision claim (Decision:C1:032)
    conn = db.get_conn()
    c12 = conn.execute("SELECT id FROM claims WHERE identity = 'Decision:C1:032' AND case_id = 1").fetchone()
    assert c12 is not None
    cid = c12["id"]

    resp = client.post(f"/case/1/claims/{cid}/decision", data={"action": "approve"})
    assert resp.status_code == 200
    resp_app = client.post("/case/1/approve-handover")
    assert resp_app.status_code == 200

    expected_note = "Disagrees with the claim at T1·013. Not yet decided by the approver."

    for persona in ["lead", "successor"]:
        login_as(client, persona)

        # 1. Screen
        page_resp = client.get("/case/1/handover")
        assert page_resp.status_code == 200
        assert expected_note in page_resp.text

        # 2. Markdown
        md_resp = client.get("/case/1/handover.md")
        assert md_resp.status_code == 200
        assert expected_note in md_resp.text
        # Assert the line is in risks section
        risks_md = md_resp.text.split("## What is at risk")[1].split("## ")[0]
        assert expected_note in risks_md
        assert "T1·013" in risks_md
        # Assert other claim's text is not leaked
        assert "Pilot launch (Saffron Lane Kitchen)" not in md_resp.text

        # 3. Print page
        pr_resp = client.get("/case/1/handover/print")
        assert pr_resp.status_code == 200
        assert expected_note in pr_resp.text


def test_answered_gap_appears_in_successor_export_and_handover_page():
    """Answer a gap as leaver, approve the attested claim and handover as approver,
    assert answer text appears in successor .md and on handover page in section for its type."""
    _seed_case_1()
    client = TestClient(main.app)

    # 1. Leaver answers gap 1 as an Owner claim
    login_as(client, "leaver")
    ans_text = "Tanvi Shetty will own on-call support during the initial pilot phase."
    resp_ans = client.post("/case/1/gaps/1/answer", data={
        "text": ans_text,
        "type": "Owner",
        "label": "team",
    })
    assert resp_ans.status_code == 200

    # 2. Approver approves the claim and approves handover
    conn = db.get_conn()
    claim = conn.execute("SELECT id FROM claims WHERE basis = 'attested' AND case_id = 1 ORDER BY id DESC LIMIT 1").fetchone()
    assert claim is not None
    cid = claim["id"]

    login_as(client, "lead")
    resp_dec = client.post(f"/case/1/claims/{cid}/decision", data={"action": "approve"})
    assert resp_dec.status_code == 200

    resp_ho = client.post("/case/1/approve-handover")
    assert resp_ho.status_code == 200

    # 3. Successor sees answer in .md and handover page under 'Who owns what'
    login_as(client, "successor")

    resp_md = client.get("/case/1/handover.md")
    assert resp_md.status_code == 200
    assert ans_text in resp_md.text
    sec_md = resp_md.text.split("## Who owns what")[1].split("## ")[0]
    assert ans_text in sec_md

    resp_page = client.get("/case/1/handover")
    assert resp_page.status_code == 200
    assert ans_text in resp_page.text
    sec_page = resp_page.text.split("<h2>Who owns what</h2>")[1].split("<h2>")[0]
    assert ans_text in sec_page


def test_cross_label_conflict_hidden_from_successor():
    """An approved claim has an open conflict with a claim the viewer is NOT cleared for (cross-label).
    For the successor, the .md, the print page and the handover page must contain no line id,
    text or note about the hidden claim.
    The 'Disagrees with the claim at ...' note may appear only when the other claim is visible
    to that person under access.visible.
    """
    _seed_case_1()
    conn = db.get_conn()
    run = repo.get_latest_run(1)
    run_id = run["id"] if run else 1

    # Insert Claim B: finance claim (hidden from successor), citing F1:005
    cur_b = conn.execute(
        """INSERT INTO claims (case_id, run_id, type, text, basis, labels, flags, identity)
           VALUES (1, ?, 'Decision', 'Budget allows 2 full-time notification engineers.', 'sourced', '["finance"]', '{}', 'cross_conflict:B')""",
        (run_id,)
    )
    cid_b = cur_b.lastrowid
    conn.execute("INSERT INTO claim_cites (claim_id, line_id, valid) VALUES (?, 'F1:005', 1)", (cid_b,))

    # Insert Claim A: team claim (visible to successor), with conflict_with [cid_b]
    flags_a = json.dumps({"conflict_with": [cid_b]})
    cur_a = conn.execute(
        """INSERT INTO claims (case_id, run_id, type, text, basis, labels, flags, identity)
           VALUES (1, ?, 'Decision', 'Notification service requires 4 engineers immediately.', 'sourced', '["team"]', ?, 'cross_conflict:A')""",
        (run_id, flags_a)
    )
    cid_a = cur_a.lastrowid
    conn.execute("INSERT INTO claim_cites (claim_id, line_id, valid) VALUES (?, 'C1:026', 1)", (cid_a,))

    # Approve Claim A
    conn.execute(
        """INSERT OR REPLACE INTO decisions (identity, case_id, decision, by, at, seeded)
           VALUES ('cross_conflict:A', 1, 'approved', 'Divya Menon', ?, 1)""",
        (db.now_iso(),)
    )
    conn.commit()

    # Re-create approved version
    review.create_version(1, approved_by="Divya Menon (seeded)", seeded=True)

    client = TestClient(main.app)

    # 1. As successor: must NOT contain F1:005, F1·005, or "Disagrees with the claim at"
    login_as(client, "successor")

    resp_md = client.get("/case/1/handover.md")
    assert resp_md.status_code == 200
    assert "F1:005" not in resp_md.text
    assert "F1·005" not in resp_md.text
    assert "Disagrees with the claim at" not in resp_md.text

    resp_print = client.get("/case/1/handover/print")
    assert resp_print.status_code == 200
    assert "F1:005" not in resp_print.text
    assert "F1·005" not in resp_print.text
    assert "Disagrees with the claim at" not in resp_print.text

    resp_ho = client.get("/case/1/handover")
    assert resp_ho.status_code == 200
    assert "F1:005" not in resp_ho.text
    assert "F1·005" not in resp_ho.text
    assert "Disagrees with the claim at" not in resp_ho.text

    # 2. As approver (who holds finance): the note DOES appear
    login_as(client, "lead")
    resp_app_md = client.get("/case/1/handover.md")
    assert resp_app_md.status_code == 200
    assert "Disagrees with the claim at F1·005. Not yet decided by the approver." in resp_app_md.text


def test_approver_md_export_logged_and_shows_in_who_has_seen_hr():
    """Download approver's .md, assert access_log row with action 'export', format 'md' and version,
    and assert 'who has seen items labelled hr' finds the approver in the answer.
    """
    _seed_case_1()
    conn = db.get_conn()
    conn.execute("DELETE FROM access_log")
    conn.commit()

    client = TestClient(main.app)

    # Approver downloads .md
    login_as(client, "lead")
    resp = client.get("/case/1/handover.md")
    assert resp.status_code == 200

    # Verify access_log row
    row = conn.execute("SELECT * FROM access_log WHERE case_id = 1 AND action = 'export' ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None
    assert row["action"] == "export"
    items = json.loads(row["items"])
    assert items["format"] == "md"
    assert items["version"] == 1
    labels_seen = json.loads(row["labels_seen"])
    assert "hr" in labels_seen

    # Ask "who has seen items labelled hr" via helper and access page
    from app.access_log import get_who_has_seen_label
    who_hr = get_who_has_seen_label(1, "hr")
    assert any(p["name"] == "Divya Menon" for p in who_hr)

    # Check via /case/1/access?label=hr
    access_resp = client.get("/case/1/access?label=hr")
    assert access_resp.status_code == 200
    assert "Divya Menon" in access_resp.text


def test_empty_section_text_and_footer_timezone():
    """1. In build_handover, a section with no approved items prints
    'No approved items in this section.' across screen, .md and print page.
    2. Footer includes timezone (UTC) on both timestamps.
    """
    _seed_case_1()
    client = TestClient(main.app)
    login_as(client, "successor")

    # In demo case, "What depends on what" (dependencies) has no approved items
    md_resp = client.get("/case/1/handover.md")
    assert md_resp.status_code == 200
    assert "No approved items in this section." in md_resp.text
    assert "## What depends on what\n\nNo approved items in this section." in md_resp.text
    assert re.search(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC.*?\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC', md_resp.text)

    # Print page
    pr_resp = client.get("/case/1/handover/print")
    assert pr_resp.status_code == 200
    assert "No approved items in this section." in pr_resp.text
    assert " UTC." in pr_resp.text

    # Handover page
    ho_resp = client.get("/case/1/handover")
    assert ho_resp.status_code == 200
    assert "No approved items in this section." in ho_resp.text
    assert " UTC." in ho_resp.text



