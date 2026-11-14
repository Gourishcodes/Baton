"""Test 1: Numbering equals tools/number_lines.py for every source.
Test 2: Every answer_key.json anchor line resolves in the parsed lines.
"""
import json
import os
import sys
import pytest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import number_lines as ref
from app import parsers

DATA = os.path.join(ROOT, "data")


class TestNumberingEquivalence:
    """SPEC 14.1: Numbering equals tools/number_lines.py for every source."""

    def test_all_sources_match(self):
        """Every source file produces identical line_id, text, and hash."""
        ref_all = ref.load_all(DATA)
        app_all = parsers.load_all(DATA)

        assert set(ref_all.keys()) == set(app_all.keys()), \
            f"Source IDs differ: ref={set(ref_all.keys())}, app={set(app_all.keys())}"

        for source_id in ref_all:
            ref_lines = ref_all[source_id]
            app_lines = app_all[source_id]

            assert len(ref_lines) == len(app_lines), \
                f"{source_id}: line count differs: ref={len(ref_lines)}, app={len(app_lines)}"

            for i, (r, a) in enumerate(zip(ref_lines, app_lines)):
                assert r["line_id"] == a["line_id"], \
                    f"{source_id} line {i}: line_id differs: ref={r['line_id']}, app={a['line_id']}"
                assert r["text"] == a["text"], \
                    f"{source_id} line {i}: text differs"
                assert r["hash"] == a["hash"], \
                    f"{source_id} line {i}: hash differs: ref={r['hash']}, app={a['hash']}"

    def test_c1_line_count(self):
        """C1 should have 49 lines per the answer key."""
        lines = parsers.load_all(DATA)["C1"]
        assert len(lines) == 49

    def test_t1_line_count(self):
        """T1 should have 14 lines per the answer key."""
        lines = parsers.load_all(DATA)["T1"]
        assert len(lines) == 14

    def test_n1_line_count(self):
        """N1 should have 29 lines per the answer key."""
        lines = parsers.load_all(DATA)["N1"]
        assert len(lines) == 29

    def test_f1_line_count(self):
        """F1 should have 7 lines per the answer key."""
        lines = parsers.load_all(DATA)["F1"]
        assert len(lines) == 7

    def test_h1_line_count(self):
        """H1 should have 8 lines per the answer key."""
        lines = parsers.load_all(DATA)["H1"]
        assert len(lines) == 8


class TestAnchorResolution:
    """SPEC 14.2: Every answer_key.json anchor line resolves in the parsed lines."""

    def test_all_anchors_resolve(self):
        """Every line_id in the answer key facts resolves to a parsed line."""
        with open(os.path.join(DATA, "answer_key.json"), encoding="utf-8") as f:
            key = json.load(f)

        all_lines = parsers.load_all(DATA)
        all_line_ids = set()
        for source_lines in all_lines.values():
            for line in source_lines:
                all_line_ids.add(line["line_id"])

        missing = []
        for fact in key["facts"]:
            for line_id in fact["line_ids"]:
                if line_id not in all_line_ids:
                    missing.append(f"fact {fact['id']}: {line_id}")
            for line_id in fact.get("also_line_ids", []):
                if line_id not in all_line_ids:
                    missing.append(f"fact {fact['id']} (also): {line_id}")

        assert not missing, f"Unresolved line IDs:\n" + "\n".join(missing)

    def test_canary_lines_resolve(self):
        """Every canary line_id resolves."""
        with open(os.path.join(DATA, "answer_key.json"), encoding="utf-8") as f:
            key = json.load(f)

        all_lines = parsers.load_all(DATA)
        all_line_ids = set()
        for source_lines in all_lines.values():
            for line in source_lines:
                all_line_ids.add(line["line_id"])

        for canary in key["canaries"]:
            for line_id in canary["line_ids"]:
                assert line_id in all_line_ids, f"Canary line {line_id} not found"
