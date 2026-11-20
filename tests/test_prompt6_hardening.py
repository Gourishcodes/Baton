import os
import sys
import tempfile
import json
import secrets
import hashlib
from datetime import datetime, timezone, timedelta
import pytest
from starlette.testclient import TestClient

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import db, repo, auth, access, review, extract
from app.main import app, not_found_response


@pytest.fixture(autouse=True)
def fresh_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db.set_db_path(tmp.name)
    db.load_demo_case()
    extract.extract_for_case(1, mode="cache_only")
    review.seed_demo_decisions(1)
    review.create_version(1, approved_by="Divya Menon (seeded)", seeded=True)
    yield
    db.close()
    try:
        os.unlink(tmp.name)
    except Exception:
        pass


def test_top_bar_no_leaves_in_on_non_demo_case():
    """Prompt 6 item 4(a): On non-demo cases, remove hard-coded 'leaves in N days' text from top bar."""
    client = TestClient(app)
    uid = auth.create_user("custom_topbar@example.com", "Custom User", auth.DEMO_PASSWORD)
    cid = repo.create_case("Top Bar Case", "Alice", "Bob", "Custom User", uid)
    auth.login_as(client, "custom_topbar@example.com")

    # Non-demo case
    r_custom = client.get(f"/case/{cid}/claims")
    assert r_custom.status_code == 200
    assert "leaves in" not in r_custom.text

    # Demo case
    client_demo = TestClient(app)
    auth.login_as(client_demo, "lead")
    r_demo = client_demo.get("/case/1/claims")
    assert r_demo.status_code == 200
    assert "leaves in" in r_demo.text


def test_approve_handover_empty_claims_banner_and_version_1():
    """Prompt 6 item 4(b): 'Approve handover' with no approved claims shows inline .banner
    'No claims are approved yet. Approve claims first, then approve the handover.' and creates no version.
    First approval on new case is version 1."""
    client = TestClient(app)
    uid = auth.create_user("approver_empty@example.com", "Appr Empty", auth.DEMO_PASSWORD)
    cid = repo.create_case("Empty Approval Case", "Alice", "Bob", "Appr Empty", uid)
    auth.login_as(client, "approver_empty@example.com")

    # 1. No claims exist or none approved: try to approve handover
    r_empty = client.post(f"/case/{cid}/approve-handover", data={"summary": "v1 attempt"})
    assert r_empty.status_code == 200
    assert "banner" in r_empty.text
    assert "No claims are approved yet. Approve claims first, then approve the handover." in r_empty.text

    # Verify no version was created
    principal = auth.Principal(user_id=uid, name="Appr Empty", email="approver_empty@example.com", case_id=cid, role="approver", is_owner=True, holds=frozenset({"team", "finance", "hr"}))
    versions_empty = repo.get_versions(cid, principal)
    assert len(versions_empty) == 0

    # 2. Add and approve a claim
    run_id = db.insert_run(cid, mode="cache_then_live", provider="spy", model="spy-model")
    db.finish_run(run_id, status="done")
    cur = db.get_conn().execute(
        "INSERT INTO claims (case_id, run_id, type, text, basis, labels, flags, identity) "
        "VALUES (?, ?, 'Decision', 'A test decision claim', 'sourced', '[\"team\"]', '{}', 'appr:c1')",
        (cid, run_id)
    )
    db.get_conn().execute(
        "INSERT INTO decisions (identity, case_id, decision, by, at, seeded) VALUES ('appr:c1', ?, 'approved', 'Appr Empty', ?, 0)",
        (cid, db.now_iso())
    )
    db.get_conn().commit()

    # Now approve handover
    r_approved = client.post(f"/case/{cid}/approve-handover", data={"summary": "First real handover"})
    assert r_approved.status_code == 200
    assert "No claims are approved yet" not in r_approved.text

    versions = repo.get_versions(cid, principal)
    assert len(versions) == 1
    assert versions[0]["n"] == 1


def test_filename_xss_escaping():
    """Prompt 6 item 3: File named <script>alert(1)</script>.txt is escaped on every page and export that shows file names."""
    conn = db.get_conn()
    xss_filename = "<script>alert(1)</script>.txt"
    conn.execute(
        "INSERT INTO sources (id, case_id, filename, kind, labels, sha256, uploaded_at, headers) "
        "VALUES ('xss_test_src', 1, ?, 'chat', '[\"team\"]', 'sha_xss', ?, '[]')",
        (xss_filename, db.now_iso())
    )
    conn.execute(
        "INSERT INTO lines (line_id, source_id, n, text, hash, labels) VALUES ('xss_test_src:001', 'xss_test_src', 1, 'Hello', 'sha_l1', '[\"team\"]')"
    )
    conn.commit()

    client = TestClient(app)
    auth.login_as(client, "lead")

    # Sources page
    r_src = client.get("/case/1/sources")
    assert r_src.status_code == 200
    assert "<script>alert(1)</script>" not in r_src.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;.txt" in r_src.text

    # Source drawer
    r_drawer = client.get("/case/1/sources/xss_test_src/drawer")
    assert r_drawer.status_code == 200
    assert "<script>alert(1)</script>" not in r_drawer.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;.txt" in r_drawer.text

    # Handover markdown export
    r_md = client.get("/case/1/handover.md")
    assert "<script>alert(1)</script>" not in r_md.text

    # Handover print export
    r_print = client.get("/case/1/handover/print")
    assert "<script>alert(1)</script>" not in r_print.text


def test_new_case_cross_label_leak_test():
    """Prompt 6 item 2: New case with team-only account vs cleared account.
    Unique random tokens under team, finance, hr.
    Team claim + finance claim in cross-label conflict.
    Team-only account must never see finance/hr tokens, line ids, finance source id, words of finance claim,
    or conflict note across any view, drawer, export, or access log.
    Cleared account sees them."""
    conn = db.get_conn()
    nc_owner_uid = auth.create_user("owner_p6@example.com", "P6 Owner", auth.DEMO_PASSWORD)
    nc_team_uid = auth.create_user("team_p6@example.com", "P6 Team User", auth.DEMO_PASSWORD)
    cid = repo.create_case("P6 Cross Label Case", "Leaver", "Successor", "P6 Owner", nc_owner_uid)

    conn.execute(
        "INSERT INTO memberships (case_id, user_id, role, is_owner, labels, added_at) VALUES (?, ?, 'viewer', 0, '[\"team\"]', ?)",
        (cid, nc_team_uid, db.now_iso())
    )
    conn.commit()

    team_tok = f"TOKEN_TEAM_{secrets.token_hex(4)}"
    fin_tok = f"TOKEN_FIN_{secrets.token_hex(4)}"
    hr_tok = f"TOKEN_HR_{secrets.token_hex(4)}"

    # Sources
    conn.execute(
        "INSERT INTO sources (id, case_id, filename, kind, labels, sha256, uploaded_at, headers) VALUES ('p6s1', ?, 'team.txt', 'chat', '[\"team\"]', 'sha_p6_1', ?, '[]')",
        (cid, db.now_iso())
    )
    conn.execute(
        "INSERT INTO lines (line_id, source_id, n, text, hash, labels) VALUES ('p6s1:001', 'p6s1', 1, ?, 'h_p6_1', '[\"team\"]')",
        (f"Vendor renewal approved at 4 lakh {team_tok}",)
    )

    conn.execute(
        "INSERT INTO sources (id, case_id, filename, kind, labels, sha256, uploaded_at, headers) VALUES ('p6s2', ?, 'finance.txt', 'notes', '[\"finance\"]', 'sha_p6_2', ?, '[]')",
        (cid, db.now_iso())
    )
    conn.execute(
        "INSERT INTO lines (line_id, source_id, n, text, hash, labels) VALUES ('p6s2:001', 'p6s2', 1, ?, 'h_p6_2', '[\"finance\"]')",
        (f"Budget cap is 3 lakh {fin_tok}",)
    )

    conn.execute(
        "INSERT INTO sources (id, case_id, filename, kind, labels, sha256, uploaded_at, headers) VALUES ('p6s3', ?, 'hr.txt', 'notes', '[\"hr\"]', 'sha_p6_3', ?, '[]')",
        (cid, db.now_iso())
    )
    conn.execute(
        "INSERT INTO lines (line_id, source_id, n, text, hash, labels) VALUES ('p6s3:001', 'p6s3', 1, ?, 'h_p6_3', '[\"hr\"]')",
        (f"Staff details with {hr_tok}",)
    )

    run_id = db.insert_run(cid, mode="cache_then_live", provider="spy", model="spy-model")
    db.finish_run(run_id, status="done")

    cur_tc = conn.execute(
        "INSERT INTO claims (case_id, run_id, type, text, subject, attribute, value, basis, labels, flags, identity) "
        "VALUES (?, ?, 'Decision', 'Vendor renewal approved at 4 lakh', 'Vendor renewal', 'amount', '4 lakh', 'sourced', '[\"team\"]', '{}', 'p6:team_claim')",
        (cid, run_id)
    )
    tc_id = cur_tc.lastrowid
    conn.execute("INSERT INTO claim_cites (claim_id, line_id, valid) VALUES (?, 'p6s1:001', 1)", (tc_id,))

    cur_fc = conn.execute(
        "INSERT INTO claims (case_id, run_id, type, text, subject, attribute, value, basis, labels, flags, identity) "
        "VALUES (?, ?, 'Decision', 'cap is 3 lakh', 'Vendor renewal', 'amount', '3 lakh', 'sourced', '[\"finance\"]', '{}', 'p6:fin_claim')",
        (cid, run_id)
    )
    fc_id = cur_fc.lastrowid
    conn.execute("INSERT INTO claim_cites (claim_id, line_id, valid) VALUES (?, 'p6s2:001', 1)", (fc_id,))

    conn.execute("UPDATE claims SET flags = ? WHERE id = ?", (json.dumps({"conflict_with": [fc_id]}), tc_id))
    conn.execute("UPDATE claims SET flags = ? WHERE id = ?", (json.dumps({"conflict_with": [tc_id]}), fc_id))

    conn.execute(
        "INSERT INTO conflicts (case_id, run_id, claim_a, claim_b, labels, possible) VALUES (?, ?, ?, ?, '[\"finance\", \"team\"]', 0)",
        (cid, run_id, tc_id, fc_id)
    )

    conn.execute(
        "INSERT INTO decisions (identity, case_id, decision, by, at, seeded) VALUES ('p6:team_claim', ?, 'approved', 'P6 Owner', ?, 0)",
        (cid, db.now_iso())
    )
    conn.execute(
        "INSERT INTO decisions (identity, case_id, decision, by, at, seeded) VALUES ('p6:fin_claim', ?, 'approved', 'P6 Owner', ?, 0)",
        (cid, db.now_iso())
    )
    review.create_version(cid, approved_by="P6 Owner")
    conn.commit()

    team_client = TestClient(app)
    auth.login_as(team_client, "team_p6@example.com")

    forbidden = [
        fin_tok,
        hr_tok,
        "p6s2:001",
        "p6s2·001",
        "p6s3:001",
        "p6s3·001",
        "p6s2",
        "cap is 3 lakh",
        "Disagrees with the claim at",
    ]

    team_views = [
        f"/case/{cid}/claims",
        f"/case/{cid}/conflicts",
        f"/case/{cid}/handover",
        f"/case/{cid}/timeline",
        f"/case/{cid}/gaps",
        f"/case/{cid}/sources",
        f"/case/{cid}/sources/p6s1/drawer",
        f"/case/{cid}/sources/p6s2/drawer",
        f"/case/{cid}/sources/p6s3/drawer",
        f"/case/{cid}/handover.md",
        f"/case/{cid}/handover/print",
        f"/case/{cid}/access",
    ]

    for tv in team_views:
        r = team_client.get(tv)
        for fb in forbidden:
            assert fb not in r.text, f"Team-only view {tv} leaked '{fb}'"

    # Cleared account positive controls
    owner_client = TestClient(app)
    auth.login_as(owner_client, "owner_p6@example.com")

    owner_ho = owner_client.get(f"/case/{cid}/handover").text
    owner_cl = owner_client.get(f"/case/{cid}/claims").text
    owner_so = owner_client.get(f"/case/{cid}/sources").text
    owner_s2 = owner_client.get(f"/case/{cid}/sources/p6s2/drawer").text
    owner_s3 = owner_client.get(f"/case/{cid}/sources/p6s3/drawer").text

    assert fin_tok in owner_s2
    assert hr_tok in owner_s3
    assert "p6s2" in owner_so
    assert "cap is 3 lakh" in owner_cl
    assert "Disagrees with the claim at" in owner_cl
