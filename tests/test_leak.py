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

from app import db, extract, review
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
