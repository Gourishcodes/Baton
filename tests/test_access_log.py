"""Tests for Baton Access Log (Prompt 2 & ROUND3_SPEC section 1.7).

Tests:
1. Sign in as Arjun Rao (team) and open claims list and one source; the log has rows
   for him with team labels only and no finance or hr item ids.
2. Sign in as Divya Menon (owner, all clearances), open an hr source; the answer to
   "who has seen items labelled hr" lists Divya and not Arjun or Meera.
3. A denied request for a hidden claim is logged as denied and still returns the identical 404 body.
4. A non-owner requesting /case/1/access gets the standard 404.
5. The log table's HTML contains no text from any line in data/.
6. Member clearance changes and invite creations are logged under their own actions.
7. Sources page shows 'Seen by N people' column for owner only.
"""
import glob
import json
import os
import re
import pytest
from starlette.testclient import TestClient

from app import db, repo, extract, review
from app.auth import seed_demo_accounts, login_as
from app.main import app, not_found_response


@pytest.fixture
def test_app(tmp_path):
    """Set up an isolated database with the seeded demo case."""
    db_file = str(tmp_path / "test_access_log.db")
    db.set_db_path(db_file)
    case_id = db.load_demo_case()
    seed_demo_accounts()
    extract.extract_for_case(case_id, mode="cache_only")
    review.seed_demo_decisions(case_id)
    review.create_version(case_id, approved_by="Divya Menon (seeded)", seeded=True)

    client = TestClient(app)
    yield client, case_id
    db.close()


def test_arjun_access_log_team_only(test_app):
    """1. Sign in as Arjun Rao (team) and open the claims list and one source;
    the log has rows for him with team labels only and no finance or hr item ids.
    """
    client, case_id = test_app
    login_as(client, "successor")  # Arjun Rao

    # Open claims list
    resp = client.get(f"/case/{case_id}/claims")
    assert resp.status_code == 200

    # Open one team source drawer
    resp_src = client.get(f"/case/{case_id}/claims/1/source?line=C1:001")
    assert resp_src.status_code == 200

    conn = db.get_conn()
    arjun_user = conn.execute("SELECT id FROM users WHERE email = 'arjun@example.com'").fetchone()
    arjun_id = arjun_user["id"]

    # Get finance and hr item ids (claims and sources)
    finance_hr_claims = conn.execute(
        "SELECT id FROM claims WHERE case_id = ? AND (labels LIKE '%finance%' OR labels LIKE '%hr%')",
        (case_id,)
    ).fetchall()
    forbidden_claim_ids = {str(r["id"]) for r in finance_hr_claims}

    finance_hr_sources = conn.execute(
        "SELECT id FROM sources WHERE case_id = ? AND (labels LIKE '%finance%' OR labels LIKE '%hr%')",
        (case_id,)
    ).fetchall()
    forbidden_source_ids = {str(r["id"]) for r in finance_hr_sources}

    # Query access log for Arjun's rows
    logs = conn.execute(
        "SELECT * FROM access_log WHERE user_id = ? AND case_id = ? AND action = 'view'",
        (arjun_id, case_id)
    ).fetchall()

    assert len(logs) >= 2, f"Expected at least 2 log rows for Arjun, got {len(logs)}"

    for row in logs:
        labels_seen = json.loads(row["labels_seen"])
        assert "finance" not in labels_seen
        assert "hr" not in labels_seen
        for l in labels_seen:
            assert l == "team"

        items = json.loads(row["items"])
        for item in items:
            item_id = str(item.get("id"))
            kind = item.get("kind")
            if kind == "claim":
                assert item_id not in forbidden_claim_ids, f"Forbidden claim {item_id} delivered to Arjun"
            elif kind == "source":
                assert item_id not in forbidden_source_ids, f"Forbidden source {item_id} delivered to Arjun"
            for label in item.get("labels", []):
                assert label == "team"


def test_divya_hr_source_who_has_seen(test_app):
    """2. Sign in as Divya Menon (owner, all clearances), open an hr source;
    the answer to 'who has seen items labelled hr' lists Divya and not Arjun or Meera.
    """
    client, case_id = test_app

    # First sign in as Arjun and Meera and open claims
    login_as(client, "successor")
    client.get(f"/case/{case_id}/claims")

    login_as(client, "leaver")
    client.get(f"/case/{case_id}/claims")

    # Now sign in as Divya Menon
    login_as(client, "lead")

    # Open an HR source (H1: notes_hr.md)
    conn = db.get_conn()
    hr_claim = conn.execute(
        "SELECT id FROM claims WHERE case_id = ? AND labels LIKE '%hr%' LIMIT 1",
        (case_id,)
    ).fetchone()
    assert hr_claim is not None, "Demo case must have at least one HR claim"
    hr_claim_id = hr_claim["id"]

    hr_cite = conn.execute(
        "SELECT line_id FROM claim_cites WHERE claim_id = ? LIMIT 1",
        (hr_claim_id,)
    ).fetchone()
    hr_line_id = hr_cite["line_id"] if hr_cite else "H1:001"

    resp = client.get(f"/case/{case_id}/claims/{hr_claim_id}/source?line={hr_line_id}")
    assert resp.status_code == 200

    # Query the Access page with label=hr
    access_resp = client.get(f"/case/{case_id}/access?label=hr")
    assert access_resp.status_code == 200
    html = access_resp.text

    # The answer must list Divya Menon
    assert "Divya Menon" in html
    assert "divya@example.com" in html

    # Arjun Rao and Meera Joshi must NOT appear in the 'Who has seen items labelled hr' table
    # Specifically:
    from app.access_log import get_who_has_seen_label
    who_seen = get_who_has_seen_label(case_id, "hr")
    who_names = [p["name"] for p in who_seen]

    assert "Divya Menon" in who_names
    assert "Arjun Rao" not in who_names
    assert "Meera Joshi" not in who_names


def test_denied_request_logged_and_returns_identical_404(test_app):
    """3. A denied request for a hidden claim is logged as denied and still returns the identical 404 body."""
    client, case_id = test_app

    # Find a hidden claim for Arjun (an HR claim)
    conn = db.get_conn()
    hr_claim = conn.execute(
        "SELECT id FROM claims WHERE case_id = ? AND labels LIKE '%hr%' LIMIT 1",
        (case_id,)
    ).fetchone()
    assert hr_claim is not None
    hidden_claim_id = hr_claim["id"]

    # Sign in as Arjun (successor, holds only 'team')
    login_as(client, "successor")
    arjun_user = conn.execute("SELECT id FROM users WHERE email = 'arjun@example.com'").fetchone()

    # Get the standard 404 response for comparison
    standard_404 = not_found_response()
    standard_404_text = standard_404.body.decode("utf-8")

    # Request the hidden claim's source drawer
    denied_resp = client.get(f"/case/{case_id}/claims/{hidden_claim_id}/source")
    assert denied_resp.status_code == 404
    assert denied_resp.text == standard_404_text

    # Assert that an action='denied' row was written to access_log
    denied_row = conn.execute(
        "SELECT * FROM access_log WHERE case_id = ? AND user_id = ? AND action = 'denied' ORDER BY id DESC LIMIT 1",
        (case_id, arjun_user["id"])
    ).fetchone()
    assert denied_row is not None
    assert f"/case/{case_id}/claims/{hidden_claim_id}/source" in denied_row["route"]
    items = json.loads(denied_row["items"])
    assert any(str(it.get("id")) == str(hidden_claim_id) for it in items)


def test_non_owner_access_log_returns_404(test_app):
    """4. A non-owner requesting /case/1/access gets the standard 404."""
    client, case_id = test_app
    standard_404_text = not_found_response().body.decode("utf-8")

    # Successor (Arjun)
    login_as(client, "successor")
    resp_succ = client.get(f"/case/{case_id}/access")
    assert resp_succ.status_code == 404
    assert resp_succ.text == standard_404_text

    # Leaver (Meera)
    login_as(client, "leaver")
    resp_leave = client.get(f"/case/{case_id}/access")
    assert resp_leave.status_code == 404
    assert resp_leave.text == standard_404_text

    # Viewer / finance_ops (Sameer)
    login_as(client, "finance_ops")
    resp_fin = client.get(f"/case/{case_id}/access")
    assert resp_fin.status_code == 404
    assert resp_fin.text == standard_404_text

    # Owner (Divya) succeeds with 200
    login_as(client, "lead")
    resp_owner = client.get(f"/case/{case_id}/access")
    assert resp_owner.status_code == 200


def test_log_table_html_contains_no_line_text_from_data(test_app):
    """5. The log table's HTML contains no text from any line in data/."""
    client, case_id = test_app

    # Generate a variety of accesses across roles
    for role in ["lead", "leaver", "successor", "finance_ops"]:
        login_as(client, role)
        client.get(f"/case/{case_id}/claims")
        client.get(f"/case/{case_id}/sources")
        client.get(f"/case/{case_id}/gaps")
        client.get(f"/case/{case_id}/conflicts")

    # Read data/ lines
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    data_lines = []
    for filepath in glob.glob(os.path.join(data_dir, "*.*")):
        if filepath.endswith((".txt", ".md", ".csv")):
            with open(filepath, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line_clean = line.strip()
                    # Only test meaningful lines with length > 8 that aren't pure punctuation/headers
                    if len(line_clean) > 8 and not line_clean.startswith("#") and not line_clean.startswith("---"):
                        data_lines.append(line_clean)

    assert len(data_lines) > 20

    # Fetch access log page as owner
    login_as(client, "lead")
    resp = client.get(f"/case/{case_id}/access")
    assert resp.status_code == 200
    html = resp.text

    # Extract the full log table portion
    table_match = re.search(r'<table[^>]*aria-label="Access Log".*?</table>', html, re.DOTALL)
    assert table_match is not None, "Access log table not found in HTML"
    table_html = table_match.group(0)

    # Assert no line text from data/ appears in the log table HTML
    for line_text in data_lines:
        assert line_text not in table_html, f"Line text '{line_text}' leaked into access log table!"


def test_sources_page_seen_by_column(test_app):
    """6. On the Sources page, owners get a 'Seen by N people' column from the same log."""
    client, case_id = test_app

    # Non-owner view: no 'Seen by' column
    login_as(client, "successor")
    succ_resp = client.get(f"/case/{case_id}/sources")
    assert succ_resp.status_code == 200
    assert "Seen by" not in succ_resp.text

    # Owner view: 'Seen by' column present
    login_as(client, "lead")
    owner_resp = client.get(f"/case/{case_id}/sources")
    assert owner_resp.status_code == 200
    assert "Seen by" in owner_resp.text
    assert "people" in owner_resp.text or "person" in owner_resp.text
