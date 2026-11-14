"""Test 6: Sticky decisions - reject a claim, re-run, it stays rejected;
         Jaccard 0.34 to 0.6 gives the 'possibly previously rejected' note.
Test 7: Cross-label conflict is invisible to successor and visible to lead and finance_ops.
"""
import json
import os
import sys
import tempfile
import pytest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

from app import db, verify, access, repo, review


@pytest.fixture(autouse=True)
def fresh_db():
    """Use a temporary database for each test."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db.set_db_path(tmp.name)
    yield
    db.close()
    os.unlink(tmp.name)


@pytest.fixture
def demo_case():
    """Load the demo case."""
    return db.load_demo_case()


class TestStickyDecisions:
    """SPEC 14.6: Sticky decisions across re-runs."""

    def test_rejected_stays_rejected(self, demo_case):
        """Reject a claim in run 1, re-run, the identical claim stays rejected."""
        case_id = demo_case

        # Run 1: create a claim and reject it
        run1 = db.insert_run(case_id)
        claim1_id = db.insert_claim(case_id, run1, {
            "type": "Decision",
            "text": "Stack is FastAPI + managed Postgres 15.",
            "basis": "unknown",
            "labels": ["team"],
            "cites": ["N1:004", "C1:006"],
        })
        verify.validate_cites(claim1_id, {"team"})
        verify.assign_basis(claim1_id)
        identity1 = verify.compute_identity(claim1_id)

        # Reject it
        review.record_decision(case_id, identity1, "rejected", by="Divya Menon")
        db.finish_run(run1, "done")

        # Run 2: create the same claim again (same type, same cites)
        run2 = db.insert_run(case_id)
        claim2_id = db.insert_claim(case_id, run2, {
            "type": "Decision",
            "text": "Stack is FastAPI + managed Postgres 15.",
            "basis": "unknown",
            "labels": ["team"],
            "cites": ["N1:004", "C1:006"],
        })
        verify.validate_cites(claim2_id, {"team"})
        verify.assign_basis(claim2_id)
        identity2 = verify.compute_identity(claim2_id)

        # Identity should be the same
        assert identity1 == identity2

        # Match prior decisions
        review.match_prior_decisions(case_id, run2)

        # The decision should carry over
        dec = review.get_decision(case_id, identity2)
        assert dec is not None
        assert dec["decision"] == "rejected"

    def test_jaccard_high_inherits(self, demo_case):
        """Jaccard >= 0.6: inherit the prior decision."""
        case_id = demo_case

        # Run 1: create a claim and approve it
        run1 = db.insert_run(case_id)
        claim1_id = db.insert_claim(case_id, run1, {
            "type": "Decision",
            "text": "Stack is FastAPI + managed Postgres 15.",
            "basis": "unknown",
            "labels": ["team"],
            "cites": ["N1:004", "C1:006"],
        })
        verify.validate_cites(claim1_id, {"team"})
        verify.assign_basis(claim1_id)
        identity1 = verify.compute_identity(claim1_id)
        review.record_decision(case_id, identity1, "approved", by="Divya Menon")
        db.finish_run(run1, "done")

        # Run 2: similar claim with overlapping cites (Jaccard should be >= 0.6)
        # Original: {N1:004, C1:006}. New: {N1:004, C1:006, C1:007} -> Jaccard = 2/3 = 0.67
        run2 = db.insert_run(case_id)
        claim2_id = db.insert_claim(case_id, run2, {
            "type": "Decision",
            "text": "Stack is FastAPI + managed Postgres 15, decided early.",
            "basis": "unknown",
            "labels": ["team"],
            "cites": ["N1:004", "C1:006", "C1:007"],
        })
        verify.validate_cites(claim2_id, {"team"})
        verify.assign_basis(claim2_id)
        verify.compute_identity(claim2_id)

        review.match_prior_decisions(case_id, run2)

        identity2 = db.get_conn().execute(
            "SELECT identity FROM claims WHERE id = ?", (claim2_id,)
        ).fetchone()["identity"]

        dec = review.get_decision(case_id, identity2)
        assert dec is not None
        assert dec["decision"] == "approved"

    def test_jaccard_low_rejected_flags(self, demo_case):
        """Jaccard 0.34-0.6 with a rejected decision: possibly_previously_rejected flag."""
        case_id = demo_case

        # Run 1: create a claim and reject it
        run1 = db.insert_run(case_id)
        claim1_id = db.insert_claim(case_id, run1, {
            "type": "Decision",
            "text": "Some decision about vendor",
            "basis": "unknown",
            "labels": ["team"],
            "cites": ["C1:006", "C1:007", "C1:008"],
        })
        verify.validate_cites(claim1_id, {"team"})
        verify.assign_basis(claim1_id)
        identity1 = verify.compute_identity(claim1_id)
        review.record_decision(case_id, identity1, "rejected", by="Divya Menon")
        db.finish_run(run1, "done")

        # Run 2: similar claim but with lower overlap (Jaccard between 0.34 and 0.6)
        # Original: {C1:006, C1:007, C1:008}. New: {C1:006, C1:009, C1:010, C1:011}
        # Jaccard = 1 / 6 = 0.167 — too low. Let me use {C1:006, C1:007, C1:009, C1:010}
        # Jaccard = 2 / 5 = 0.4 — in range!
        run2 = db.insert_run(case_id)
        claim2_id = db.insert_claim(case_id, run2, {
            "type": "Decision",
            "text": "Similar decision about vendor",
            "basis": "unknown",
            "labels": ["team"],
            "cites": ["C1:006", "C1:007", "C1:009", "C1:010"],
        })
        verify.validate_cites(claim2_id, {"team"})
        verify.assign_basis(claim2_id)
        verify.compute_identity(claim2_id)

        review.match_prior_decisions(case_id, run2)

        conn = db.get_conn()
        flags = json.loads(conn.execute(
            "SELECT flags FROM claims WHERE id = ?", (claim2_id,)
        ).fetchone()["flags"])

        assert flags.get("possibly_previously_rejected") is True

    def test_edited_decision_locked(self, demo_case):
        """An edited decision should be locked."""
        case_id = demo_case

        run1 = db.insert_run(case_id)
        claim1_id = db.insert_claim(case_id, run1, {
            "type": "Decision",
            "text": "Original text.",
            "basis": "unknown",
            "labels": ["team"],
            "cites": ["C1:006"],
        })
        verify.validate_cites(claim1_id, {"team"})
        verify.assign_basis(claim1_id)
        identity = verify.compute_identity(claim1_id)
        review.record_decision(case_id, identity, "edited",
                               edited_text="Corrected text.", by="Divya Menon")

        assert review.is_decision_locked(case_id, identity) is True


class TestCrossLabelConflictVisibility:
    """SPEC 14.7: Cross-label conflict is invisible to successor and visible
    to lead and finance_ops."""

    def test_cross_label_conflict_visibility(self, demo_case):
        """X02: Cross-label conflict needs team+finance clearance."""
        case_id = demo_case
        run_id = db.insert_run(case_id)

        # Create two claims that form a cross-label conflict
        a_id = db.insert_claim(case_id, run_id, {
            "type": "Decision",
            "text": "Infra budget is about 41500",
            "subject": "infra budget",
            "attribute": "budget_monthly",
            "value": "41500",
            "basis": "sourced",
            "labels": ["team"],
            "cites": ["C1:021"],
        })

        b_id = db.insert_claim(case_id, run_id, {
            "type": "Decision",
            "text": "Infra cap drops to 24750",
            "subject": "infra budget",
            "attribute": "budget_monthly",
            "value": "24750",
            "basis": "sourced",
            "labels": ["finance"],
            "cites": ["F1:005"],
        })

        verify.detect_conflicts(run_id, case_id)

        # Check visibility
        successor_conflicts = repo.get_conflicts(case_id, "successor")
        lead_conflicts = repo.get_conflicts(case_id, "lead")
        finance_conflicts = repo.get_conflicts(case_id, "finance_ops")

        # Successor sees no visible conflicts (team+finance label set)
        visible_successor = [c for c in successor_conflicts if c.get("visible")]
        assert len(visible_successor) == 0, \
            "Successor should not see cross-label conflict"

        # Lead sees the conflict
        visible_lead = [c for c in lead_conflicts if c.get("visible")]
        assert len(visible_lead) == 1, \
            "Lead should see cross-label conflict"

        # Finance_ops sees the conflict
        visible_finance = [c for c in finance_conflicts if c.get("visible")]
        assert len(visible_finance) == 1, \
            "Finance_ops should see cross-label conflict"

    def test_same_label_conflict_visible_to_all(self, demo_case):
        """X01: Same-label conflict is visible to everyone who holds team."""
        case_id = demo_case
        run_id = db.insert_run(case_id)

        a_id = db.insert_claim(case_id, run_id, {
            "type": "Decision",
            "text": "Pilot launch date is 28 Nov",
            "subject": "pilot launch",
            "attribute": "due_date",
            "value": "2026-11-28",
            "basis": "sourced",
            "labels": ["team"],
            "cites": ["C1:032"],
        })

        b_id = db.insert_claim(case_id, run_id, {
            "type": "Decision",
            "text": "Pilot launch date is 5 Dec",
            "subject": "pilot launch",
            "attribute": "due_date",
            "value": "2026-12-05",
            "basis": "sourced",
            "labels": ["team"],
            "cites": ["T1:013"],
        })

        verify.detect_conflicts(run_id, case_id)

        for persona in ["leaver", "successor", "lead", "finance_ops"]:
            conflicts = repo.get_conflicts(case_id, persona)
            visible = [c for c in conflicts if c.get("visible")]
            assert len(visible) == 1, \
                f"{persona} should see same-label conflict"
