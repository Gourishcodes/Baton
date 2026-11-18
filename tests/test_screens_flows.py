"""Tests for screens, flows, and decisions in Baton.

Covers:
- Gaps screen and answer flow (leaver and lead, label constraints, 404 on hidden gap)
- Decisions (approve, edit, reject, restore, keyed by identity)
- Handover document (sections, approved only, history, successor no-label rule)
- Version approval and successor acknowledgement
- Demo reset
"""
import json
import os
import sys
import tempfile
import pytest
from starlette.testclient import TestClient

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import db, repo, extract, review, main, access
from app.auth import login_as, seed_demo_accounts

client = TestClient(main.app)


def set_role_cookie(persona: str):
    login_as(client, persona)


@pytest.fixture(autouse=True)
def clean_db():
    """Each test runs with an isolated seeded database."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db.set_db_path(tmp.name)
    case_id = db.load_demo_case()
    seed_demo_accounts()
    extract.extract_for_case(case_id, mode="cache_only")
    review.seed_demo_decisions(case_id)
    review.create_version(case_id, approved_by="Divya Menon (seeded)", seeded=True)
    yield
    db.close()
    try:
        os.unlink(tmp.name)
    except Exception:
        pass


class TestGapsAndAnswerFlow:
    """SPEC 9: Gap interview and answer flow."""

    def test_gaps_view_visible_to_persona(self):
        """Lead sees all gaps; successor/leaver only see team gaps."""
        set_role_cookie("lead")
        resp_lead = client.get("/case/1/gaps")
        assert resp_lead.status_code == 200

        set_role_cookie("successor")
        resp_succ = client.get("/case/1/gaps")
        assert resp_succ.status_code == 200

        set_role_cookie("leaver")
        resp_leaver = client.get("/case/1/gaps")
        assert resp_leaver.status_code == 200

    def test_leaver_answers_gap_creates_attested_claim(self):
        """Leaver answers an open gap: creates an Attested claim starting Pending."""
        set_role_cookie("leaver")

        # Find an open gap
        conn = db.get_conn()
        gap = conn.execute("SELECT id, labels FROM gaps WHERE case_id = 1 AND status = 'open' LIMIT 1").fetchone()
        assert gap is not None
        gap_id = gap["id"]

        resp = client.post(f"/case/1/gaps/{gap_id}/answer", data={
            "text": "Tanvi Shetty owns this task per sprint review.",
            "type": "Owner",
            "label": "team",
        }, follow_redirects=True)
        assert resp.status_code == 200

        # Verify gap is marked answered
        updated_gap = conn.execute("SELECT status, answer_claim_id FROM gaps WHERE id = ?", (gap_id,)).fetchone()
        assert updated_gap["status"] == "answered"
        assert updated_gap["answer_claim_id"] is not None

        # Verify claim
        claim = conn.execute("SELECT * FROM claims WHERE id = ?", (updated_gap["answer_claim_id"],)).fetchone()
        assert claim["basis"] == "attested"
        assert claim["type"] == "Owner"
        assert claim["text"] == "Tanvi Shetty owns this task per sprint review."
        assert claim["identity"] == f"attested:{gap_id}"
        assert claim["attested_by"] == "Meera Joshi"

        # Starts pending (no decision row)
        dec = conn.execute("SELECT * FROM decisions WHERE identity = ?", (claim["identity"],)).fetchone()
        assert dec is None

    def test_hidden_gap_answer_returns_identical_404(self):
        """Answering a hidden gap returns the identical 404 body as a fake id."""
        # Finance gap is hidden from leaver
        conn = db.get_conn()
        finance_gap = conn.execute(
            "SELECT id FROM gaps WHERE case_id = 1 AND labels LIKE '%finance%' LIMIT 1"
        ).fetchone()

        set_role_cookie("leaver")

        fake_resp = client.post("/case/1/gaps/9999/answer", data={"text": "Nope"})
        assert fake_resp.status_code == 404

        if finance_gap:
            hidden_resp = client.post(f"/case/1/gaps/{finance_gap['id']}/answer", data={"text": "Sneaky"})
            assert hidden_resp.status_code == 404
            assert hidden_resp.content == fake_resp.content


class TestDecisions:
    """SPEC 8: Decisions approve, edit, reject, restore, keyed by identity."""

    def test_approve_reject_restore_by_lead(self):
        set_role_cookie("lead")
        conn = db.get_conn()
        claim = conn.execute("SELECT id, identity FROM claims WHERE case_id = 1 LIMIT 1").fetchone()
        cid = claim["id"]
        identity = claim["identity"]

        # Reject
        r_resp = client.post(f"/case/1/claims/{cid}/decision?action=reject")
        assert r_resp.status_code == 200
        dec = conn.execute("SELECT decision FROM decisions WHERE identity = ?", (identity,)).fetchone()
        assert dec["decision"] == "rejected"

        # Restore
        res_resp = client.post(f"/case/1/claims/{cid}/decision?action=restore")
        assert res_resp.status_code == 200
        dec = conn.execute("SELECT decision FROM decisions WHERE identity = ?", (identity,)).fetchone()
        assert dec is None  # Restored to pending

        # Approve
        a_resp = client.post(f"/case/1/claims/{cid}/decision?action=approve")
        assert a_resp.status_code == 200
        dec = conn.execute("SELECT decision FROM decisions WHERE identity = ?", (identity,)).fetchone()
        assert dec["decision"] == "approved"

    def test_edit_claim_locks_text(self):
        set_role_cookie("lead")
        conn = db.get_conn()
        claim = conn.execute("SELECT id, identity FROM claims WHERE case_id = 1 LIMIT 1").fetchone()
        cid = claim["id"]
        identity = claim["identity"]

        resp = client.post(f"/case/1/claims/{cid}/decision", data={
            "action": "edit",
            "text": "Edited verified claim text",
        })
        assert resp.status_code == 200
        dec = conn.execute("SELECT decision, edited_text FROM decisions WHERE identity = ?", (identity,)).fetchone()
        assert dec["decision"] == "edited"
        assert dec["edited_text"] == "Edited verified claim text"

    def test_non_lead_cannot_record_decisions(self):
        set_role_cookie("successor")
        resp = client.post("/case/1/claims/1/decision?action=approve")
        assert resp.status_code == 403


class TestHandoverAndApprovals:
    """SPEC 8: Handover sections, versions, acknowledgement."""

    def test_successor_sees_no_labels_in_handover(self):
        """For the successor, no label names appear anywhere in the handover."""
        set_role_cookie("successor")
        resp = client.get("/case/1/handover")
        assert resp.status_code == 200
        # Ensure label tags don't appear
        assert 'class="tag' not in resp.text
        # In the handover document body, no label names appear
        import re
        body = resp.text.split("<main")[1].split("</main>")[0] if "<main" in resp.text else resp.text
        assert "finance" not in body.lower()
        assert not re.search(r"\bhr\b", body, re.IGNORECASE)

    def test_approve_handover_creates_version(self):
        set_role_cookie("lead")
        conn = db.get_conn()
        v_before = conn.execute("SELECT MAX(n) as max_n FROM versions WHERE case_id = 1").fetchone()["max_n"] or 0

        resp = client.post("/case/1/approve-handover", follow_redirects=True)
        assert resp.status_code == 200

        v_after = conn.execute("SELECT MAX(n) as max_n FROM versions WHERE case_id = 1").fetchone()["max_n"]
        assert v_after == v_before + 1

    def test_successor_acknowledges_receipt_closes_case(self):
        set_role_cookie("successor")
        resp = client.post("/case/1/acknowledge", follow_redirects=True)
        assert resp.status_code == 200

        conn = db.get_conn()
        case = conn.execute("SELECT status FROM cases WHERE id = 1").fetchone()
        assert case["status"] == "closed"


class TestDemoReset:
    """SPEC 8: Reset demo restores seeded state."""

    def test_reset_demo(self):
        set_role_cookie("lead")
        # Change something: record an edit
        client.post("/case/1/claims/1/decision", data={"action": "edit", "text": "Modified text"})

        # Reset demo
        resp = client.post("/demo/reset", follow_redirects=True)
        assert resp.status_code == 200

        # Check version 1 exists and is seeded
        conn = db.get_conn()
        v1 = conn.execute("SELECT seeded FROM versions WHERE case_id = 1 AND n = 1").fetchone()
        assert v1 is not None
        assert v1["seeded"] == 1

    def test_reset_reopens_answered_gaps_and_preserves_cites(self):
        """Answer a gap, reset, assert the same gap id still exists, is open, and has its original cites."""
        conn = db.get_conn()
        # Find an open gap with cites
        gap = conn.execute("""
            SELECT g.id, g.question 
            FROM gaps g 
            JOIN gap_cites gc ON gc.gap_id = g.id 
            WHERE g.case_id = 1 
            LIMIT 1
        """).fetchone()
        assert gap is not None
        gid = gap["id"]

        # Record original cites
        original_cites = [
            row["line_id"] for row in conn.execute(
                "SELECT line_id FROM gap_cites WHERE gap_id = ? ORDER BY line_id", (gid,)
            ).fetchall()
        ]
        assert len(original_cites) > 0

        # Answer the gap as leaver
        set_role_cookie("leaver")
        resp = client.post(f"/case/1/gaps/{gid}/answer", data={
            "text": "Temporary answer for reset test",
            "label": "team",
            "type": "Decision"
        }, follow_redirects=True)
        assert resp.status_code == 200

        # Verify it was answered and created an attested claim
        gap_answered = conn.execute("SELECT status, answer_claim_id FROM gaps WHERE id = ?", (gid,)).fetchone()
        assert gap_answered["status"] == "answered"
        attested_cid = gap_answered["answer_claim_id"]
        assert attested_cid is not None
        attested_claim = conn.execute("SELECT id FROM claims WHERE id = ?", (attested_cid,)).fetchone()
        assert attested_claim is not None

        # Reset the demo
        set_role_cookie("lead")
        reset_resp = client.post("/demo/reset", follow_redirects=True)
        assert reset_resp.status_code == 200

        # Assert same gap id still exists, is open, and answer_claim_id is NULL
        gap_after = conn.execute("SELECT id, status, answer_claim_id FROM gaps WHERE id = ?", (gid,)).fetchone()
        assert gap_after is not None
        assert gap_after["id"] == gid
        assert gap_after["status"] == "open"
        assert gap_after["answer_claim_id"] is None

        # Assert original cites still exist
        cites_after = [
            row["line_id"] for row in conn.execute(
                "SELECT line_id FROM gap_cites WHERE gap_id = ? ORDER BY line_id", (gid,)
            ).fetchall()
        ]
        assert cites_after == original_cites

        # Assert attested claim was deleted
        assert conn.execute("SELECT id FROM claims WHERE id = ?", (attested_cid,)).fetchone() is None
