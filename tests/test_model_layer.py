"""Tests for model layer, extraction, caching, and eval.

Tests required by Prompt 2:
- The schema validator rejects bad output and retries once
- A provider failure falls back to the other provider
- cache_only makes zero network calls (fails if any network connection is attempted)
- eval runs from fixtures with no model and returns structured results
"""
import os
import socket
import sys
from typing import Any
import pytest
from pydantic import ValidationError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import extract, eval as app_eval
from app.llm.base import Provider
from app.llm.gemini import GeminiProvider
from app.llm.openai_compat import OpenAICompatProvider


class MockProvider(Provider):
    """Test mock provider that returns canned responses or raises exceptions."""

    def __init__(self, responses: list[dict | Exception]):
        self.responses = list(responses)
        self.call_count = 0
        self.calls: list[dict[str, Any]] = []

    def generate_json(self, system: str, user: str, schema: dict) -> dict:
        self.call_count += 1
        self.calls.append({"system": system, "user": user, "schema": schema})
        if not self.responses:
            raise RuntimeError("No more mock responses configured")
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


VALID_EXTRACTION_JSON = {
    "claims": [
        {
            "type": "Decision",
            "text": "Valid test claim sentence.",
            "subject": "test subject",
            "attribute": "status",
            "value": "active",
            "owner": "test.user",
            "due": None,
            "event_date": None,
            "recurring": False,
            "not_on_task_board": False,
            "supersedes": None,
            "cites": ["C1:001"],
        }
    ],
    "gaps": [
        {
            "question": "Who owns this task?",
            "why": "Missing assignee",
            "cites": ["T1:001"],
        }
    ],
}


class TestExtractionValidationAndFallback:
    """Tests for schema validation, retries, and provider fallback."""

    def test_schema_validator_rejects_bad_output_and_retries_once(self, tmp_path):
        """Provider returns invalid output on 1st call; retries once and succeeds on 2nd call."""
        invalid_json = {"claims": [{"text": "Missing type, subject, and attribute"}]}
        provider = MockProvider([invalid_json, VALID_EXTRACTION_JSON])

        dummy_lines = [{"line_id": "C1:001", "text": "Test line"}]
        result = extract.extract_for_label_set(
            label_set="test",
            lines=dummy_lines,
            mode="live_only",
            primary_provider=provider,
            fallback_provider=None,
            cache_dir=str(tmp_path),
        )

        assert provider.call_count == 2, f"Expected 2 attempts (1 retry), got {provider.call_count}"
        assert len(result["claims"]) == 1
        assert result["claims"][0]["type"] == "Decision"

    def test_provider_failure_falls_back(self, tmp_path):
        """Primary fails on both attempts; secondary provider is called and succeeds."""
        primary = MockProvider([
            RuntimeError("Primary 1st failure"),
            RuntimeError("Primary 2nd failure"),
        ])
        fallback = MockProvider([VALID_EXTRACTION_JSON])

        dummy_lines = [{"line_id": "C1:001", "text": "Test line"}]
        result = extract.extract_for_label_set(
            label_set="test_fallback",
            lines=dummy_lines,
            mode="live_only",
            primary_provider=primary,
            fallback_provider=fallback,
            cache_dir=str(tmp_path),
        )

        assert primary.call_count == 2, "Primary should have tried twice"
        assert fallback.call_count == 1, "Fallback should have been invoked"
        assert len(result["claims"]) == 1
        assert result["claims"][0]["subject"] == "test subject"

    def test_both_providers_fail_raises_runtime_error(self, tmp_path):
        """When both primary and fallback fail on all retries, raise clear RuntimeError."""
        primary = MockProvider([RuntimeError("P1"), RuntimeError("P2")])
        fallback = MockProvider([RuntimeError("F1"), RuntimeError("F2")])

        dummy_lines = [{"line_id": "C1:001", "text": "Test line"}]
        with pytest.raises(RuntimeError) as exc_info:
            extract.extract_for_label_set(
                label_set="test_double_fail",
                lines=dummy_lines,
                mode="live_only",
                primary_provider=primary,
                fallback_provider=fallback,
                cache_dir=str(tmp_path),
            )

        assert "Extraction failed" in str(exc_info.value)
        assert primary.call_count == 2
        assert fallback.call_count == 2


class TestCacheOnlyZeroNetworkCalls:
    """SPEC 11: cache_only must never open a network connection."""

    def test_cache_only_makes_zero_network_calls(self, monkeypatch, tmp_path):
        """Assert that attempting extraction in cache_only mode never touches network sockets."""
        def forbidden_connect(*args, **kwargs):
            pytest.fail("Network socket connection attempted during cache_only mode!")

        monkeypatch.setattr(socket.socket, "connect", forbidden_connect)

        # 1. Existing cache or fixture loads with zero network calls
        real_lines = [{"line_id": "C1:001", "text": "[2026-10-01 10:00] user: hello"}]
        # For an unknown label set with empty cache, it must raise RuntimeError without network calls
        with pytest.raises(RuntimeError) as exc_info:
            extract.extract_for_label_set(
                label_set="nonexistent_label_set_xyz",
                lines=real_lines,
                mode="cache_only",
                cache_dir=str(tmp_path),
            )
        assert "Network connection prohibited" in str(exc_info.value)

    def test_cache_key_is_sha256(self):
        """Verify cache key computation is sha256 string."""
        lines = [
            {"line_id": "C1:002", "text": "line two"},
            {"line_id": "C1:001", "text": "line one"},
        ]
        key = extract.compute_cache_key("team", lines)
        assert len(key) == 64
        # Deterministic
        assert key == extract.compute_cache_key("team", list(reversed(lines)))


class TestEvalPipeline:
    """SPEC 12: eval runs from fixtures with no model and prints report."""

    def test_eval_runs_from_fixtures(self, tmp_path):
        """Run eval in cache_only mode from fixtures and check all expected outputs."""
        results = app_eval.run_eval(mode="cache_only", cache_dir=str(tmp_path))

        assert "recall" in results
        assert "precision" in results
        assert "citation_validity" in results
        assert "traps" in results
        assert "string_check_clean" in results

        rec = results["recall"]
        assert rec["total"] == 37
        assert rec["matched"] >= 35, "Recall should match key facts"

        assert results["string_check_clean"] is True

        # Check traps
        traps = {t["id"]: t for t in results["traps"]}
        assert len(traps) == 7
        for tid in ["TR01", "TR02", "TR03", "TR04", "TR05", "TR06", "TR07"]:
            assert tid in traps
            assert traps[tid]["pass"] is True, f"{tid} failed: {traps[tid]['evidence']}"

    def test_eval_report_banner_fixtures(self, tmp_path):
        """When source is fixtures, print the fixtures banner and counts only, omitting Overall Recall headline."""
        results = app_eval.run_eval(mode="cache_only", cache_dir=str(tmp_path))
        report = app_eval.format_report(results)

        assert "SOURCE: fixtures. SELF-TEST OF THE VERIFY AND ACCESS CODE. THIS IS NOT MODEL ACCURACY." in report
        assert "Overall Recall:" not in report
        assert "Non-optional key facts: 37 of 37" in report

    def test_eval_report_banner_demo_cache(self, monkeypatch, tmp_path):
        """When source is demo_cache, print the demo_cache banner with provider/model and Overall Recall headline."""
        import json
        from app import parsers

        monkeypatch.setenv("LLM_PROVIDER", "gemini")
        monkeypatch.setenv("LLM_MODEL", "gemini-2.5-flash")

        # Write cache files into tmp_path
        data_dir = os.path.join(ROOT, "data")
        with open(os.path.join(data_dir, "roles.json"), encoding="utf-8") as f:
            roles_data = json.load(f)
        all_sources = parsers.load_all(data_dir)
        source_labels = {s["id"]: s["labels"] for s in roles_data["sources"]}

        groups: dict[str, list[dict]] = {}
        for sid, lines in all_sources.items():
            lkey = ",".join(sorted(source_labels[sid]))
            groups.setdefault(lkey, []).extend(lines)

        for lkey, lines in groups.items():
            ckey = extract.compute_cache_key(lkey, lines)
            fixture_data = extract.load_fixture_for_label_set(lkey)
            with open(tmp_path / f"{ckey}.json", "w", encoding="utf-8") as f:
                json.dump(fixture_data, f)

        results = app_eval.run_eval(mode="cache_only", cache_dir=str(tmp_path))
        report = app_eval.format_report(results)

        assert "SOURCE: demo_cache (gemini, gemini-2.5-flash)" in report
        assert "Overall Recall:" in report


class TestProvidersEnvironmentConfiguration:
    """Verify providers take model and key only from env, never hardcoded."""

    def test_gemini_provider_reads_env(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test_key_gemini")
        monkeypatch.setenv("LLM_MODEL", "custom-gemini-model")
        p = GeminiProvider()
        assert p.api_key == "test_key_gemini"
        assert p.model == "custom-gemini-model"

    def test_openai_provider_reads_env(self, monkeypatch):
        monkeypatch.setenv("FALLBACK_BASE_URL", "https://api.groq.com/openai/v1")
        monkeypatch.setenv("FALLBACK_API_KEY", "test_key_fallback")
        monkeypatch.setenv("FALLBACK_MODEL", "llama-3.3-70b-versatile")
        p = OpenAICompatProvider()
        assert p.base_url == "https://api.groq.com/openai/v1"
        assert p.api_key == "test_key_fallback"
        assert p.model == "llama-3.3-70b-versatile"
