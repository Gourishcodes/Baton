"""Kernel demo: load fixtures and run the full verification pipeline.

Run as: python -m app.kernel_demo

Prints:
- Claims per basis
- Conflicts found
- Which persona sees which key fact (visibility check against answer_key.json)
- Defect detection results (fake cite, cross-label cite, no-cite claim)
"""
import json
import os
import sys
import tempfile

# Force UTF-8 output on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

# Add project root to path
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

from app import db, verify, access, repo, review


FIXTURES_DIR = os.path.join(ROOT, "tests", "fixtures")
DATA_DIR = os.path.join(ROOT, "data")


def load_fixture(label_name: str) -> dict:
    path = os.path.join(FIXTURES_DIR, f"model_output_{label_name}.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_answer_key() -> dict:
    path = os.path.join(DATA_DIR, "answer_key.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def ingest_fixture(case_id: int, run_id: int, fixture: dict, call_labels: list[str]):
    """Load a fixture's claims and gaps into the database."""
    conn = db.get_conn()

    for claim_data in fixture["claims"]:
        flags = {}
        if claim_data.get("supersedes"):
            flags["supersedes_info"] = claim_data["supersedes"]

        db.insert_claim(case_id, run_id, {
            "type": claim_data["type"],
            "text": claim_data["text"],
            "subject": claim_data.get("subject"),
            "attribute": claim_data.get("attribute", "none"),
            "value": claim_data.get("value"),
            "owner": claim_data.get("owner"),
            "due": claim_data.get("due"),
            "event_date": claim_data.get("event_date"),
            "basis": "unknown",  # Will be assigned by verification
            "labels": call_labels,
            "flags": flags,
            "cites": claim_data.get("cites", []),
        })

    for gap_data in fixture["gaps"]:
        db.insert_gap(case_id, run_id, {
            "question": gap_data["question"],
            "why": gap_data.get("why"),
            "labels": call_labels,
            "cites": gap_data.get("cites", []),
            "status": "open",
        })


def main():
    # Use a temporary database
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db.set_db_path(tmp.name)

    try:
        # Load the demo case
        case_id = db.load_demo_case(DATA_DIR)
        run_id = db.insert_run(case_id, mode="fixture", provider="fixture", model="fixture")

        # Ingest all fixtures
        for label_name, call_labels in [("team", ["team"]), ("finance", ["finance"]), ("hr", ["hr"])]:
            fixture = load_fixture(label_name)
            ingest_fixture(case_id, run_id, fixture, call_labels)

        # Build call_label_sets
        call_label_sets = {
            "team": {"team"},
            "finance": {"finance"},
            "hr": {"hr"},
        }

        # Run verification pipeline
        verify.run_verification_pipeline(case_id, run_id, call_label_sets)
        db.finish_run(run_id, "done")

        conn = db.get_conn()

        # === Report: Claims per basis ===
        print("=" * 60)
        print("CLAIMS PER BASIS")
        print("=" * 60)
        for basis in ["sourced", "inferred", "unknown"]:
            count = conn.execute(
                "SELECT COUNT(*) as cnt FROM claims WHERE run_id = ? AND basis = ?",
                (run_id, basis)
            ).fetchone()["cnt"]
            print(f"  {basis}: {count}")
        print()

        # === Report: Conflicts found ===
        print("=" * 60)
        print("CONFLICTS FOUND")
        print("=" * 60)
        conflicts = conn.execute(
            "SELECT c.*, ca.text as a_text, ca.subject as a_subj, ca.value as a_val, ca.labels as a_labels, "
            "cb.text as b_text, cb.subject as b_subj, cb.value as b_val, cb.labels as b_labels "
            "FROM conflicts c "
            "JOIN claims ca ON c.claim_a = ca.id "
            "JOIN claims cb ON c.claim_b = cb.id "
            "WHERE c.case_id = ?",
            (case_id,)
        ).fetchall()
        for c in conflicts:
            possible_str = " (POSSIBLE)" if c["possible"] else ""
            labels = json.loads(c["labels"])
            print(f"  Conflict #{c['id']}{possible_str} labels={labels}")
            print(f"    A: [{c['a_subj']}] = {c['a_val']} (labels={c['a_labels']})")
            print(f"    B: [{c['b_subj']}] = {c['b_val']} (labels={c['b_labels']})")
        print(f"  Total: {len(conflicts)} conflicts")
        print()

        # === Report: Supersession ===
        print("=" * 60)
        print("SUPERSESSION")
        print("=" * 60)
        superseded = conn.execute(
            "SELECT id, text, flags FROM claims WHERE run_id = ? AND json_extract(flags, '$.superseded_by') IS NOT NULL",
            (run_id,)
        ).fetchall()
        for s in superseded:
            flags = json.loads(s["flags"])
            print(f"  Claim #{s['id']}: superseded by #{flags['superseded_by']}")
            print(f"    Text: {s['text'][:80]}")
        superseding = conn.execute(
            "SELECT id, text, flags FROM claims WHERE run_id = ? AND json_extract(flags, '$.supersedes') IS NOT NULL",
            (run_id,)
        ).fetchall()
        for s in superseding:
            flags = json.loads(s["flags"])
            print(f"  Claim #{s['id']}: supersedes #{flags['supersedes']}")
            print(f"    Text: {s['text'][:80]}")
        print()

        # === Report: Defect detection ===
        print("=" * 60)
        print("DEFECT DETECTION")
        print("=" * 60)

        defect_claims = conn.execute(
            "SELECT id, text, basis FROM claims WHERE run_id = ? AND text LIKE 'DEFECT:%'",
            (run_id,)
        ).fetchall()

        for dc in defect_claims:
            cites = conn.execute(
                "SELECT line_id, valid FROM claim_cites WHERE claim_id = ?",
                (dc["id"],)
            ).fetchall()
            cite_info = [(c["line_id"], "VALID" if c["valid"] else "INVALID") for c in cites]
            print(f"  Claim #{dc['id']}: basis={dc['basis']}")
            print(f"    Text: {dc['text']}")
            print(f"    Cites: {cite_info}")
        print()

        # === Report: Visibility check against answer key ===
        print("=" * 60)
        print("VISIBILITY CHECK (vs answer_key.json)")
        print("=" * 60)
        answer_key = load_answer_key()
        personas = ["leaver", "successor", "lead", "finance_ops"]
        mismatches = []

        for fact in answer_key["facts"]:
            fact_id = fact["id"]
            expected_visible = fact["visible_to"]

            for persona in personas:
                expected = expected_visible.get(persona, False)
                found = False

                if fact["type"] == "Conflict":
                    # For conflict facts, check the conflicts table visibility
                    all_conflicts = repo.get_conflicts(case_id, persona)
                    for conflict in all_conflicts:
                        if not conflict.get("visible", False):
                            continue
                        # Check if this conflict's claims cite the fact's lines
                        a_data = conflict.get("claim_a_data")
                        b_data = conflict.get("claim_b_data")
                        if a_data and b_data:
                            a_cites = {c["line_id"] for c in a_data.get("cites", []) if c.get("valid")}
                            b_cites = {c["line_id"] for c in b_data.get("cites", []) if c.get("valid")}
                            all_cites = a_cites | b_cites
                            if all_cites & set(fact["line_ids"]):
                                found = True
                                break
                elif fact["type"] == "Gap":
                    all_gaps = repo.get_gaps(case_id, persona)
                    for gap in all_gaps:
                        if not gap.get("visible", False):
                            continue
                        gap_cite_ids = {c["line_id"] for c in gap.get("cites", []) if c.get("valid")}
                        if gap_cite_ids & set(fact["line_ids"]):
                            found = True
                            break
                else:
                    # Regular claims: check if any visible claim cites at least one fact line_id
                    all_claims = repo.get_claims(case_id, persona, run_id=run_id)
                    for claim in all_claims:
                        if not claim.get("visible", False):
                            continue
                        claim_cite_ids = {c["line_id"] for c in claim.get("cites", []) if c.get("valid")}
                        if claim_cite_ids & set(fact["line_ids"]):
                            found = True
                            break

                if found != expected:
                    mismatches.append(f"  {fact_id} [{persona}]: expected={expected}, found={found}")

        if mismatches:
            print(f"  MISMATCHES: {len(mismatches)}")
            for m in mismatches:
                print(m)
        else:
            print("  Zero visibility mismatches!")
        print()

        # === Summary ===
        print("=" * 60)
        print("SUMMARY")
        print("=" * 60)
        total_claims = conn.execute(
            "SELECT COUNT(*) as cnt FROM claims WHERE run_id = ?", (run_id,)
        ).fetchone()["cnt"]
        total_gaps = conn.execute(
            "SELECT COUNT(*) as cnt FROM gaps WHERE run_id = ?", (run_id,)
        ).fetchone()["cnt"]
        print(f"  Total claims: {total_claims}")
        print(f"  Total gaps: {total_gaps}")
        print(f"  Total conflicts: {len(conflicts)}")
        print(f"  Visibility mismatches: {len(mismatches)}")

    finally:
        db.close()
        os.unlink(tmp.name)


if __name__ == "__main__":
    main()
