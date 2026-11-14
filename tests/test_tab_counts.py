"""Test 8: Tab counts come from persona-filtered repository functions.

For each persona, each tab's count equals the number of rows that persona
can open on that tab (the Claims tab count equals visible rows plus redacted stubs).
"""
import os
import sys
import tempfile
import pytest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import db, repo, extract, review, main


@pytest.fixture(scope="module")
def seeded_case():
    """Create a seeded case for testing tab counts."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db.set_db_path(tmp.name)
    case_id = db.load_demo_case()
    extract.extract_for_case(case_id, mode="cache_only")
    review.seed_demo_decisions(case_id)
    review.create_version(case_id, approved_by="Divya Menon (seeded)", seeded=True)
    yield case_id
    db.close()
    try:
        os.unlink(tmp.name)
    except Exception:
        pass


PERSONAS = ["lead", "successor", "leaver", "finance_ops"]


class TestTabCounts:
    """Every tab count comes from persona-filtered repo functions."""

    @pytest.mark.parametrize("persona", PERSONAS)
    def test_tab_counts_equal_openable_rows(self, seeded_case, persona):
        """For each persona, each tab's count equals the number of rows that persona
        can open on that tab (the Claims tab count equals visible rows plus redacted stubs).
        """
        case_id = seeded_case
        counts = main.get_nav_counts(case_id, persona)

        # 1. Sources count equals sources visible to persona
        sources = repo.get_sources(case_id, persona)
        assert counts["sources"] == len(sources), f"Sources count mismatch for {persona}"

        # 2. Claims count equals visible claims + redacted stubs
        claims = repo.get_claims(case_id, persona)
        visible_claims = [c for c in claims if c.get("visible", False)]
        redacted_stubs = [c for c in claims if not c.get("visible", False)]
        assert len(claims) == len(visible_claims) + len(redacted_stubs)
        assert counts["claims"] == len(claims), f"Claims count mismatch for {persona}"

        # 3. Gaps count equals gaps visible to persona (openable rows)
        gaps = repo.get_gaps(case_id, persona)
        assert counts["gaps"] == len(gaps), f"Gaps count mismatch for {persona}"
        for g in gaps:
            assert g.get("visible", True) is True, f"Non-visible gap found in {persona} gaps"

        # 4. Conflicts count equals conflicts visible to persona (openable rows)
        conflicts = repo.get_conflicts(case_id, persona)
        assert counts["conflicts"] == len(conflicts), f"Conflicts count mismatch for {persona}"
        for c in conflicts:
            assert c.get("visible", True) is True, f"Non-visible conflict found in {persona} conflicts"

        # 5. Approvals count equals version history rows
        versions = repo.get_versions(case_id, persona)
        assert counts["approvals"] == len(versions), f"Approvals count mismatch for {persona}"

    def test_persona_specific_counts_differ_by_clearance(self, seeded_case):
        """Approver sees full counts; Successor and Leaver see team clearance counts only."""
        case_id = seeded_case
        lead_counts = main.get_nav_counts(case_id, "lead")
        succ_counts = main.get_nav_counts(case_id, "successor")
        leaver_counts = main.get_nav_counts(case_id, "leaver")

        # Lead sees all 5 sources; successor sees 3 team sources
        assert lead_counts["sources"] == 5
        assert succ_counts["sources"] == 3
        assert leaver_counts["sources"] == 3

        # Lead sees 7 gaps; successor sees 5 team gaps
        assert lead_counts["gaps"] == 7
        assert succ_counts["gaps"] == 5
        assert leaver_counts["gaps"] == 5

        # Both lead and successor have 35 claims on the ledger (lead has 35 visible, 0 stubs; successor has 24 visible, 11 stubs)
        assert lead_counts["claims"] == 35
        assert succ_counts["claims"] == 35
