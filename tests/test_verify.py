"""Test 3: Verifier marks a fabricated cite invalid and the claim Inferred.
Test 4: A cite to a line outside the call's label set is invalid.
"""
import json
import os
import sys
import tempfile
import pytest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

from app import db, verify


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
    case_id = db.load_demo_case()
    return case_id


class TestVerifier:
    """SPEC 14.3: Verifier marks a fabricated cite invalid and the claim Inferred."""

    def test_fabricated_cite_invalid(self, demo_case):
        """A claim citing a non-existent line should have that cite marked invalid."""
        case_id = demo_case
        run_id = db.insert_run(case_id)

        claim_id = db.insert_claim(case_id, run_id, {
            "type": "Decision",
            "text": "Some claim with a fake cite",
            "basis": "unknown",
            "labels": ["team"],
            "cites": ["FAKE:999"],
        })

        verify.validate_cites(claim_id, {"team"})
        basis = verify.assign_basis(claim_id)

        conn = db.get_conn()
        cite = conn.execute(
            "SELECT valid FROM claim_cites WHERE claim_id = ? AND line_id = 'FAKE:999'",
            (claim_id,)
        ).fetchone()

        assert cite["valid"] == 0, "Fabricated cite should be invalid"
        assert basis == "inferred", "Claim with only invalid cites should be inferred"

    def test_valid_cite_sourced(self, demo_case):
        """A claim citing a real team line should be sourced."""
        case_id = demo_case
        run_id = db.insert_run(case_id)

        claim_id = db.insert_claim(case_id, run_id, {
            "type": "Decision",
            "text": "Some claim with a real cite",
            "basis": "unknown",
            "labels": ["team"],
            "cites": ["C1:001"],
        })

        verify.validate_cites(claim_id, {"team"})
        basis = verify.assign_basis(claim_id)

        assert basis == "sourced"

    def test_no_cites_inferred(self, demo_case):
        """A claim with no cites at all should be inferred."""
        case_id = demo_case
        run_id = db.insert_run(case_id)

        claim_id = db.insert_claim(case_id, run_id, {
            "type": "Decision",
            "text": "Some claim with no cites",
            "basis": "unknown",
            "labels": ["team"],
            "cites": [],
        })

        verify.validate_cites(claim_id, {"team"})
        basis = verify.assign_basis(claim_id)

        assert basis == "inferred"


class TestCrossLabelCite:
    """SPEC 14.4: A cite to a line outside the call's label set is invalid."""

    def test_cross_label_cite_invalid(self, demo_case):
        """A team call's claim citing a finance line should be invalid."""
        case_id = demo_case
        run_id = db.insert_run(case_id)

        claim_id = db.insert_claim(case_id, run_id, {
            "type": "Decision",
            "text": "Team claim citing finance line",
            "basis": "unknown",
            "labels": ["team"],
            "cites": ["F1:001"],
        })

        verify.validate_cites(claim_id, {"team"})  # team call
        basis = verify.assign_basis(claim_id)

        conn = db.get_conn()
        cite = conn.execute(
            "SELECT valid FROM claim_cites WHERE claim_id = ? AND line_id = 'F1:001'",
            (claim_id,)
        ).fetchone()

        assert cite["valid"] == 0, "Cross-label cite should be invalid"
        assert basis == "inferred", "Claim with only cross-label cites should be inferred"

    def test_same_label_cite_valid(self, demo_case):
        """A finance call's claim citing a finance line should be valid."""
        case_id = demo_case
        run_id = db.insert_run(case_id)

        claim_id = db.insert_claim(case_id, run_id, {
            "type": "Decision",
            "text": "Finance claim citing finance line",
            "basis": "unknown",
            "labels": ["finance"],
            "cites": ["F1:001"],
        })

        verify.validate_cites(claim_id, {"finance"})  # finance call
        basis = verify.assign_basis(claim_id)

        conn = db.get_conn()
        cite = conn.execute(
            "SELECT valid FROM claim_cites WHERE claim_id = ? AND line_id = 'F1:001'",
            (claim_id,)
        ).fetchone()

        assert cite["valid"] == 1, "Same-label cite should be valid"
        assert basis == "sourced"


class TestSupersession:
    """Test supersession detection."""

    def test_supersession_detected(self, demo_case):
        """D03 should supersede D02 when both have matching subject/attribute."""
        case_id = demo_case
        run_id = db.insert_run(case_id)

        # D02: old SMS vendor
        d02_id = db.insert_claim(case_id, run_id, {
            "type": "Decision",
            "text": "Textly was chosen as the SMS vendor",
            "subject": "sms vendor",
            "attribute": "vendor",
            "value": "textly",
            "basis": "unknown",
            "labels": ["team"],
            "flags": {},
            "cites": ["C1:008"],
        })

        # D03: new SMS vendor, supersedes D02
        d03_id = db.insert_claim(case_id, run_id, {
            "type": "Decision",
            "text": "SMS moved from Textly to Pingwire",
            "subject": "sms vendor",
            "attribute": "vendor",
            "value": "pingwire",
            "basis": "unknown",
            "labels": ["team"],
            "flags": {"supersedes_info": {
                "subject": "sms vendor",
                "attribute": "vendor",
                "old_value": "textly",
            }},
            "cites": ["C1:026", "N1:014"],
        })

        verify.validate_cites(d02_id, {"team"})
        verify.validate_cites(d03_id, {"team"})
        verify.detect_supersession(run_id, case_id)

        conn = db.get_conn()
        d02_flags = json.loads(conn.execute(
            "SELECT flags FROM claims WHERE id = ?", (d02_id,)
        ).fetchone()["flags"])
        d03_flags = json.loads(conn.execute(
            "SELECT flags FROM claims WHERE id = ?", (d03_id,)
        ).fetchone()["flags"])

        assert d02_flags.get("superseded_by") == d03_id
        assert d03_flags.get("supersedes") == d02_id


class TestConflictDetection:
    """Test conflict detection across label sets."""

    def test_same_label_conflict(self, demo_case):
        """X01: Two claims with same subject/attribute but different values in same label set."""
        case_id = demo_case
        run_id = db.insert_run(case_id)

        a_id = db.insert_claim(case_id, run_id, {
            "type": "Decision",
            "text": "Pilot launch date is 28 Nov",
            "subject": "pilot launch",
            "attribute": "due_date",
            "value": "2026-11-28",
            "basis": "unknown",
            "labels": ["team"],
            "cites": ["C1:032"],
        })

        b_id = db.insert_claim(case_id, run_id, {
            "type": "Decision",
            "text": "Pilot launch date is 5 Dec",
            "subject": "pilot launch",
            "attribute": "due_date",
            "value": "2026-12-05",
            "basis": "unknown",
            "labels": ["team"],
            "cites": ["T1:013"],
        })

        verify.detect_conflicts(run_id, case_id)

        conn = db.get_conn()
        conflicts = conn.execute(
            "SELECT * FROM conflicts WHERE case_id = ?", (case_id,)
        ).fetchall()

        assert len(conflicts) == 1
        assert not conflicts[0]["possible"]
        assert json.loads(conflicts[0]["labels"]) == ["team"]

    def test_cross_label_conflict(self, demo_case):
        """X02: Two claims with same subject/attribute across label sets."""
        case_id = demo_case
        run_id = db.insert_run(case_id)

        a_id = db.insert_claim(case_id, run_id, {
            "type": "Decision",
            "text": "Infra budget is about 41500",
            "subject": "infra budget",
            "attribute": "budget_monthly",
            "value": "41500",
            "basis": "unknown",
            "labels": ["team"],
            "cites": ["C1:021"],
        })

        b_id = db.insert_claim(case_id, run_id, {
            "type": "Decision",
            "text": "Infra cap drops to 24750",
            "subject": "infra budget",
            "attribute": "budget_monthly",
            "value": "24750",
            "basis": "unknown",
            "labels": ["finance"],
            "cites": ["F1:005"],
        })

        verify.detect_conflicts(run_id, case_id)

        conn = db.get_conn()
        conflicts = conn.execute(
            "SELECT * FROM conflicts WHERE case_id = ?", (case_id,)
        ).fetchall()

        assert len(conflicts) == 1
        conflict_labels = json.loads(conflicts[0]["labels"])
        assert set(conflict_labels) == {"team", "finance"}

    def test_superseded_not_conflict(self, demo_case):
        """Superseded claims should not be conflict candidates."""
        case_id = demo_case
        run_id = db.insert_run(case_id)

        a_id = db.insert_claim(case_id, run_id, {
            "type": "Decision",
            "text": "Old vendor is Textly",
            "subject": "sms vendor",
            "attribute": "vendor",
            "value": "textly",
            "basis": "unknown",
            "labels": ["team"],
            "flags": {"superseded_by": 999},  # Already superseded
            "cites": ["C1:008"],
        })

        b_id = db.insert_claim(case_id, run_id, {
            "type": "Decision",
            "text": "New vendor is Pingwire",
            "subject": "sms vendor",
            "attribute": "vendor",
            "value": "pingwire",
            "basis": "unknown",
            "labels": ["team"],
            "cites": ["C1:026"],
        })

        verify.detect_conflicts(run_id, case_id)

        conn = db.get_conn()
        conflicts = conn.execute(
            "SELECT * FROM conflicts WHERE case_id = ?", (case_id,)
        ).fetchall()

        assert len(conflicts) == 0


class TestDeterministicGaps:
    """Test deterministic gap generation from task CSV."""

    def test_generates_gaps_for_unassigned_tasks(self, demo_case):
        """Should generate gaps for task rows with empty assignee and not Done/Cancelled."""
        case_id = demo_case
        run_id = db.insert_run(case_id)

        gap_ids = verify.generate_deterministic_gaps(case_id, run_id)

        # T-09 (Todo, no assignee), T-16 (Todo, no assignee), T-21 (Todo, no assignee)
        assert len(gap_ids) == 3, f"Expected 3 deterministic gaps, got {len(gap_ids)}"

        conn = db.get_conn()
        questions = []
        for gid in gap_ids:
            gap = conn.execute("SELECT question FROM gaps WHERE id = ?", (gid,)).fetchone()
            questions.append(gap["question"])

        assert any("T-09" in q for q in questions)
        assert any("T-16" in q for q in questions)
        assert any("T-21" in q for q in questions)

    def test_done_tasks_excluded(self, demo_case):
        """Done and Cancelled tasks should not generate gaps even with no assignee."""
        case_id = demo_case
        run_id = db.insert_run(case_id)

        gap_ids = verify.generate_deterministic_gaps(case_id, run_id)

        conn = db.get_conn()
        for gid in gap_ids:
            gap = conn.execute("SELECT question FROM gaps WHERE id = ?", (gid,)).fetchone()
            # None of these should be for Done or Cancelled tasks
            assert "T-03" not in gap["question"]
            assert "T-05" not in gap["question"]


class TestDropClosedTasks:
    """Test dropping NextAction claims citing only closed tasks."""

    def test_drop_closed_tasks_rule(self, demo_case):
        """NextAction claims whose cites are all Done or Cancelled tasks must be dropped."""
        case_id = demo_case
        run_id = db.insert_run(case_id)

        # 1. Closed task (T1:001 is Done) -> Should be dropped
        c1 = db.insert_claim(case_id, run_id, {
            "type": "NextAction",
            "text": "Closed action done",
            "basis": "sourced",
            "labels": ["team"],
            "cites": ["T1:001"],
        })

        # 2. Closed task (T1:002 is Cancelled) -> Should be dropped
        c2 = db.insert_claim(case_id, run_id, {
            "type": "NextAction",
            "text": "Closed action cancelled",
            "basis": "sourced",
            "labels": ["team"],
            "cites": ["T1:002"],
        })

        # 3. Active task (T1:008 is In progress) -> Must NOT be dropped
        c3 = db.insert_claim(case_id, run_id, {
            "type": "NextAction",
            "text": "Active task in progress",
            "basis": "sourced",
            "labels": ["team"],
            "cites": ["T1:008"],
        })

        # 4. Non-NextAction claim (Decision citing T1:001) -> Must NOT be dropped
        c4 = db.insert_claim(case_id, run_id, {
            "type": "Decision",
            "text": "Postgres was chosen",
            "basis": "sourced",
            "labels": ["team"],
            "cites": ["T1:001"],
        })

        # 5. Mixed cite (T1:001 and chat line C1:004) -> Must NOT be dropped
        c5 = db.insert_claim(case_id, run_id, {
            "type": "NextAction",
            "text": "Mixed cite action",
            "basis": "sourced",
            "labels": ["team"],
            "cites": ["T1:001", "C1:004"],
        })

        dropped = verify.drop_closed_task_claims(case_id, run_id)
        assert len(dropped) == 2
        assert dropped[0]["text"] == "Closed action done"
        assert dropped[0]["cites"] == ["T1:001"]
        assert dropped[0]["reason"] == "cites only closed tasks"
        assert dropped[1]["text"] == "Closed action cancelled"
        assert dropped[1]["cites"] == ["T1:002"]
        assert dropped[1]["reason"] == "cites only closed tasks"

        conn = db.get_conn()
        remaining = conn.execute(
            "SELECT id FROM claims WHERE case_id = ? AND run_id = ?",
            (case_id, run_id)
        ).fetchall()
        remaining_ids = {r["id"] for r in remaining}

        assert c1 not in remaining_ids
        assert c2 not in remaining_ids
        assert c3 in remaining_ids
        assert c4 in remaining_ids
        assert c5 in remaining_ids

        # Check dropped_claims table
        dc_rows = conn.execute(
            "SELECT text, cites, reason FROM dropped_claims WHERE case_id = ?",
            (case_id,)
        ).fetchall()
        assert len(dc_rows) == 2
        assert json.loads(dc_rows[0]["cites"]) == ["T1:001"]
        assert dc_rows[0]["reason"] == "cites only closed tasks"

