"""Test 8 (SPEC 14.8): Leak test returns zero and fails when a canary is
injected into a template (prove the test can fail).

Also covers basic leak test execution (returns zero under normal conditions).
"""
import os
import sys
import tempfile
import pytest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import db, extract, review, repo
from app.auth import demo_principal
from app.leaktest import run_leak_test


@pytest.fixture(autouse=True)
def fresh_db():
    """Use a temporary database for each test."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db.set_db_path(tmp.name)
    yield
    db.close()
    try:
        os.unlink(tmp.name)
    except Exception:
        pass


class TestLeakTest:
    """SPEC 14.8: Leak test returns zero and can detect injected canaries."""

    def test_leak_test_returns_zero(self):
        """Under normal conditions, the leak test finds zero forbidden canaries."""
        results = run_leak_test(verbose=False)
        assert results["total_found"] == 0, (
            f"Leak test found {results['total_found']} forbidden canaries"
        )
        assert results["total_scanned"] > 0, (
            "Leak test should scan at least some strings"
        )
        assert len(results["stub_failures"]) == 0, (
            f"Stub failures: {results['stub_failures']}"
        )
        assert len(results["label_failures"]) == 0, (
            f"Label failures: {results['label_failures']}"
        )

    def test_leak_test_reports_count_never_safe(self):
        """The leak test report never uses the word 'safe'."""
        from app.leaktest import format_report
        results = run_leak_test(verbose=False)
        report = format_report(results)
        assert "safe" not in report.lower(), (
            "Leak test report must never use the word 'safe'"
        )
        assert "scanned" in report.lower(), (
            "Report should mention 'scanned'"
        )

    def test_leak_test_fails_on_injected_canary(self):
        """Prove the leak test can fail: inject a canary into a template,
        run the test, assert it catches the leak, then restore the template."""
        # The canary "PW-CONTRACT-7731" is a finance canary that
        # leaver and successor must not see.
        canary = "PW-CONTRACT-7731"

        # Read the original template
        template_path = os.path.join(ROOT, "templates", "home.html")
        with open(template_path, "r", encoding="utf-8") as f:
            original = f.read()

        try:
            # Inject the canary into the home template
            injected = original.replace(
                "{% block content %}",
                "{% block content %}\n<!-- " + canary + " -->\n"
            )
            with open(template_path, "w", encoding="utf-8") as f:
                f.write(injected)

            # Run the leak test - it should find the canary
            results = run_leak_test(verbose=False)

            # The wire check middleware should either block the response (500)
            # or the canary should be found in the scan.
            # Either way, total_found should be > 0 for restricted personas
            # viewing the home page, OR the middleware blocks it entirely.
            home_findings = [
                r for r in results["rows"]
                if r["view"] == "/" and r["persona"] in ("leaver", "successor")
            ]

            # The middleware will block it (return 500) so the canary won't
            # appear in the body. But we can check that the response was blocked.
            # Let's check via the status code or by checking total_found
            blocked = any(r["status_code"] == 500 for r in home_findings)
            found = any(r["found"] > 0 for r in home_findings)

            assert blocked or found, (
                f"Injected canary '{canary}' was neither blocked by middleware "
                f"nor detected by leak scanner. home_findings={home_findings}"
            )

        finally:
            # Always restore the original template
            with open(template_path, "w", encoding="utf-8") as f:
                f.write(original)

    def test_positive_controls(self):
        """Canaries a persona IS allowed to see DO appear for that persona."""
        results = run_leak_test(verbose=False)
        pc = results.get("positive_controls", [])
        # Lead should see all canaries since they hold all labels
        lead_controls = [p for p in pc if p["persona"] == "lead"]
        assert len(lead_controls) > 0, (
            "There should be positive controls for the lead persona"
        )
        all_present = all(p["present"] for p in lead_controls)
        assert all_present, (
            f"Not all expected canaries appeared for lead: "
            f"{[p for p in lead_controls if not p['present']]}"
        )

    def test_trust_caption_scanned_count_matches_leaktest_report(self):
        """The caption under the counter on /trust shows the real scanned count from leak test."""
        import subprocess
        import re
        from starlette.testclient import TestClient
        from app.main import app

        # 1. Run python -m app.leaktest and extract the reported count
        proc = subprocess.run(
            [sys.executable, "-m", "app.leaktest"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        assert proc.returncode == 0, f"app.leaktest failed:\n{proc.stderr}"
        match_cli = re.search(r"Strings scanned:\s*(\d+)", proc.stdout)
        assert match_cli is not None, f"Could not find 'Strings scanned' in CLI output:\n{proc.stdout}"
        cli_scanned_count = int(match_cli.group(1))

        # 2. Fetch /trust page and extract the scanned count from the caption under the counter
        client = TestClient(app)
        from app.auth import login_as
        login_as(client, "lead")
        resp = client.get("/trust")
        assert resp.status_code == 200

        caption_match = re.search(r"(\d+)\s+strings scanned", resp.text)
        assert caption_match is not None, f"Could not find 'X strings scanned' in /trust response:\n{resp.text}"
        caption_scanned_count = int(caption_match.group(1))

        # 3. Assert caption's scanned count equals the count python -m app.leaktest reports
        assert caption_scanned_count == cli_scanned_count, (
            f"Caption scanned count ({caption_scanned_count}) != CLI reported count ({cli_scanned_count})"
        )

        # 4. Assert "A strong check, not a proof." is kept
        assert "A strong check, not a proof." in resp.text

    def test_post_leak_test_shows_table_and_timestamp_preserves_claims(self):
        """After POST /trust/leak-test, /trust shows persona table, positive-controls, timestamp, and preserves claims."""
        from starlette.testclient import TestClient
        from app.main import app
        import re

        # Ensure demo case is loaded and seeded in current DB
        db.load_demo_case()
        extract.extract_for_case(1, mode="cache_only")
        review.seed_demo_decisions(1)
        review.create_version(1, approved_by="Divya Menon (seeded)", seeded=True)

        p_lead = demo_principal("lead", case_id=1)
        lead_claims_before = len(repo.get_claims(1, p_lead))
        lead_gaps_before = len(repo.get_gaps(1, p_lead))
        case_status_before = repo.get_case(1, p_lead)["status"]
        assert lead_claims_before > 0

        client = TestClient(app)
        from app.auth import login_as
        login_as(client, "lead")

        # Standard POST + redirect
        resp = client.post("/trust/leak-test", follow_redirects=True)
        assert resp.status_code == 200

        # Assert persona table exists with columns and personas
        assert "Persona" in resp.text
        assert "Strings scanned" in resp.text
        assert "leaver" in resp.text
        assert "successor" in resp.text
        assert "lead" in resp.text
        assert "finance_ops" in resp.text

        # Assert positive-controls line
        assert "Positive controls:" in resp.text
        assert "expected canaries present" in resp.text

        # Assert timestamp (run at HH:MM:SS)
        assert re.search(r"run at \d{2}:\d{2}:\d{2}", resp.text) is not None

        # Assert HTMX partial swap returns result block
        hx_resp = client.post("/trust/leak-test", headers={"HX-Request": "true"})
        assert hx_resp.status_code == 200
        assert "Persona" in hx_resp.text
        assert "Positive controls:" in hx_resp.text
        assert re.search(r"run at \d{2}:\d{2}:\d{2}", hx_resp.text) is not None

        # Never use the word "safe"
        assert "safe" not in resp.text.lower()
        assert "safe" not in hx_resp.text.lower()

        # Assert claim counts and state for lead are unchanged
        lead_claims_after = len(repo.get_claims(1, p_lead))
        lead_gaps_after = len(repo.get_gaps(1, p_lead))
        case_status_after = repo.get_case(1, p_lead)["status"]

        assert lead_claims_after == lead_claims_before
        assert lead_gaps_after == lead_gaps_before
        assert case_status_after == case_status_before

    def test_post_eval_shows_table_and_timestamp_preserves_claims(self):
        """After POST /trust/eval, /trust shows recall/precision/TR01-TR07 rows, timestamp, and preserves claims."""
        from starlette.testclient import TestClient
        from app.main import app
        import re

        # Ensure demo case is loaded and seeded in current DB
        db.load_demo_case()
        extract.extract_for_case(1, mode="cache_only")
        review.seed_demo_decisions(1)
        review.create_version(1, approved_by="Divya Menon (seeded)", seeded=True)

        p_lead = demo_principal("lead", case_id=1)
        lead_claims_before = len(repo.get_claims(1, p_lead))
        lead_gaps_before = len(repo.get_gaps(1, p_lead))
        case_status_before = repo.get_case(1, p_lead)["status"]
        assert lead_claims_before > 0

        client = TestClient(app)
        from app.auth import login_as
        login_as(client, "lead")

        # Standard POST + redirect
        resp = client.post("/trust/eval", follow_redirects=True)
        assert resp.status_code == 200

        # Assert recall, precision, and TR01 to TR07 rows
        assert "Recall against the answer key" in resp.text
        assert "Claims matching a key fact" in resp.text
        assert "TR01: superseded decision" in resp.text
        assert "TR07: untracked promise" in resp.text

        # Assert timestamp (run at HH:MM:SS)
        assert re.search(r"run at \d{2}:\d{2}:\d{2}", resp.text) is not None

        # Assert HTMX partial swap returns result block
        hx_resp = client.post("/trust/eval", headers={"HX-Request": "true"})
        assert hx_resp.status_code == 200
        assert "Recall against the answer key" in hx_resp.text
        assert "TR01: superseded decision" in hx_resp.text
        assert re.search(r"run at \d{2}:\d{2}:\d{2}", hx_resp.text) is not None

        # Never use the word "safe"
        assert "safe" not in resp.text.lower()
        assert "safe" not in hx_resp.text.lower()

        # Assert claim counts and state for lead are unchanged
        lead_claims_after = len(repo.get_claims(1, p_lead))
        lead_gaps_after = len(repo.get_gaps(1, p_lead))
        case_status_after = repo.get_case(1, p_lead)["status"]

        assert lead_claims_after == lead_claims_before
        assert lead_gaps_after == lead_gaps_before
        assert case_status_after == case_status_before
