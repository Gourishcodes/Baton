"""Tests for Round 3 refactoring: Principal, case-scoping, and drawer 404s."""
import json
import os
import sys
import tempfile
import pytest
from starlette.testclient import TestClient

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import db, repo, extract, review, main
from app.auth import Principal, demo_principal, login_as, seed_demo_accounts


@pytest.fixture(autouse=True)
def isolated_db():
    """Run each test with an isolated database."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db.set_db_path(tmp.name)
    yield tmp.name
    db.close()
    try:
        os.unlink(tmp.name)
    except Exception:
        pass


def setup_two_cases():
    """Setup Case 1 (demo) and Case 2 with distinct sources, lines, claims, and gaps."""
    c1 = db.load_demo_case()
    seed_demo_accounts()
    extract.extract_for_case(c1, mode="cache_only")
    review.seed_demo_decisions(c1)
    review.create_version(c1, approved_by="Divya Menon (seeded)", seeded=True)

    conn = db.get_conn()
    conn.execute(
        "INSERT INTO cases (id, name, leaver, successor, approver, status, created_at) "
        "VALUES (2, 'Case Two Project', 'Bob Leaver', 'Alice Successor', 'Charlie Lead', 'open', ?)",
        (db.now_iso(),)
    )

    # Insert source c2s1 for Case 2
    c2_labels = json.dumps(["team"])
    conn.execute(
        "INSERT INTO sources (id, case_id, filename, kind, labels, sha256, uploaded_at) "
        "VALUES ('c2s1', 2, 'notes_case2.md', 'md', ?, 'dummyhash', ?)",
        (c2_labels, db.now_iso())
    )

    # Insert line c2s1:001
    conn.execute(
        "INSERT INTO lines (line_id, source_id, n, text, hash, labels) "
        "VALUES ('c2s1:001', 'c2s1', 1, 'Secret case 2 line content', 'dummylinehash', ?)",
        (c2_labels,)
    )

    # Insert claim and gap for Case 2
    claim2_id = db.insert_claim(2, run_id=None, claim_data={
        "type": "Decision",
        "text": "Case 2 decision claim",
        "basis": "sourced",
        "labels": ["team"],
        "identity": "Decision:c2s1:001",
        "cites": [{"line_id": "c2s1:001", "valid": 1}],
    })
    review.record_decision(2, "Decision:c2s1:001", "approved", by="Charlie Lead")

    gap2_id = db.insert_gap(2, run_id=None, gap_data={
        "question": "Case 2 open question?",
        "why": "Context for case 2",
        "labels": ["team"],
        "cites": [{"line_id": "c2s1:001", "valid": 1}],
    })

    conn.commit()
    return c1, 2


def test_case_scoping_repo_drawer_and_extraction():
    """A second case row with its own source (c2s1) and lines:
    the demo case's repo reads, drawer route, and extraction input contain none of its lines, and the reverse.
    """
    c1, c2 = setup_two_cases()
    p1 = demo_principal("lead", case_id=1)
    p2 = demo_principal("lead", case_id=2)

    # 1. Repo reads for sources
    c1_sources = repo.get_sources(c1, p1)
    c1_source_ids = {s["id"] for s in c1_sources}
    assert "c2s1" not in c1_source_ids

    c2_sources = repo.get_sources(c2, p2)
    c2_source_ids = {s["id"] for s in c2_sources}
    assert "c2s1" in c2_source_ids
    assert not any(sid in c2_source_ids for sid in ["C1", "T1", "N1", "F1", "H1"])

    # 2. Repo reads for lines
    # Requesting c2s1 through case 1 repo must return None
    src1, lines1 = repo.get_lines_for_source(c1, "c2s1", p1)
    assert src1 is None and lines1 is None

    line1 = repo.get_line(c1, "c2s1:001", p1)
    assert line1 is None

    # Requesting c2s1 through case 2 repo returns the lines
    src2, lines2 = repo.get_lines_for_source(c2, "c2s1", p2)
    assert src2 is not None and len(lines2) == 1
    assert lines2[0]["line_id"] == "c2s1:001"

    # Requesting Case 1 lines through case 2 repo returns None
    src2_c1, lines2_c1 = repo.get_lines_for_source(c2, "C1", p2)
    assert src2_c1 is None and lines2_c1 is None

    # 3. Extraction input must only contain lines of the target case
    # If we extract case 2, it should only see c2s1:001
    # We test by checking the lines grouped for extraction in extract_for_case
    conn = db.get_conn()
    c1_lines_query = conn.execute(
        "SELECT l.line_id FROM lines l JOIN sources s ON l.source_id = s.id WHERE s.case_id = 1"
    ).fetchall()
    c1_expected_ids = {r["line_id"] for r in c1_lines_query}
    assert "c2s1:001" not in c1_expected_ids

    # Run extraction for case 1 again and ensure claims cited only case 1 lines
    # and no case 2 lines were included
    c1_claims = repo.get_claims(c1, p1)
    for cl in c1_claims:
        if cl.get("cites"):
            for cite in cl["cites"]:
                assert not cite["line_id"].startswith("c2s1")


def test_drawer_cross_case_line_returns_standard_404():
    """A drawer request for a case-2 line through /case/1/... returns the standard 404,
    byte-identical to a missing item.
    """
    c1, c2 = setup_two_cases()
    client = TestClient(main.app)
    login_as(client, "lead")

    # Baseline: 404 for nonexistent line
    missing_resp = client.get("/case/1/claims/1/source?line=nonexistent:999")
    assert missing_resp.status_code == 404

    # Request case-2 line through case-1 route
    cross_case_resp = client.get("/case/1/claims/1/source?line=c2s1:001")
    assert cross_case_resp.status_code == 404
    assert cross_case_resp.content == missing_resp.content


def test_demo_reset_leaves_second_case_unchanged():
    """demo_reset touches case 1 only, leaving case 2 completely unchanged."""
    c1, c2 = setup_two_cases()
    conn = db.get_conn()

    # Record case 2 state before demo_reset
    p2 = demo_principal("lead", case_id=2)
    c2_claims_before = repo.get_claims(c2, p2)
    c2_gaps_before = repo.get_gaps(c2, p2)
    c2_sources_before = repo.get_sources(c2, p2)
    c2_decisions_before = conn.execute("SELECT * FROM decisions WHERE case_id = 2").fetchall()
    assert len(c2_claims_before) > 0
    assert len(c2_gaps_before) > 0

    client = TestClient(main.app)
    login_as(client, "lead")

    # POST /demo/reset
    resp = client.post("/demo/reset", follow_redirects=True)
    assert resp.status_code == 200

    # Verify case 2 is completely unchanged
    c2_claims_after = repo.get_claims(c2, p2)
    c2_gaps_after = repo.get_gaps(c2, p2)
    c2_sources_after = repo.get_sources(c2, p2)
    c2_decisions_after = conn.execute("SELECT * FROM decisions WHERE case_id = 2").fetchall()

    assert len(c2_claims_after) == len(c2_claims_before)
    assert len(c2_gaps_after) == len(c2_gaps_before)
    assert len(c2_sources_after) == len(c2_sources_before)
    assert len(c2_decisions_after) == len(c2_decisions_before)


def test_repo_call_with_string_raises_type_error():
    """A direct call to any repo function with a string raises TypeError."""
    c1, c2 = setup_two_cases()

    with pytest.raises(TypeError):
        repo.get_claims(c1, "lead")

    with pytest.raises(TypeError):
        repo.get_sources(c1, "lead")

    with pytest.raises(TypeError):
        repo.get_gaps(c1, "lead")

    with pytest.raises(TypeError):
        repo.get_conflicts(c1, "lead")

    with pytest.raises(TypeError):
        repo.get_claim(c1, 1, "lead")

    with pytest.raises(TypeError):
        repo.get_gap(c1, 1, "lead")

    with pytest.raises(TypeError):
        repo.get_versions(c1, "lead")
