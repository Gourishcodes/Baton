"""Verification pipeline for Baton claims.

Plain code, no model. Implements SPEC section 6:
- Cite validation (line exists AND belongs to call's label set)
- Basis assignment (sourced or inferred)
- Supersession detection
- Conflict detection across ALL label sets
- Deterministic gaps from empty-assignee task rows
"""
import csv
import io
import json
import re

from . import db


def validate_cites(claim_id: int, call_labels: set[str], conn=None):
    """Validate each cite for a claim: valid only if the line exists AND
    belongs to a source in this call's label set.

    Returns (valid_count, total_count).
    """
    if conn is None:
        conn = db.get_conn()

    cites = conn.execute(
        "SELECT rowid, claim_id, line_id FROM claim_cites WHERE claim_id = ?",
        (claim_id,)
    ).fetchall()

    valid_count = 0
    for cite in cites:
        line = conn.execute(
            "SELECT line_id, labels FROM lines WHERE line_id = ?",
            (cite["line_id"],)
        ).fetchone()

        if line is None:
            # Line doesn't exist
            conn.execute(
                "UPDATE claim_cites SET valid = 0 WHERE claim_id = ? AND line_id = ?",
                (cite["claim_id"], cite["line_id"])
            )
        else:
            line_labels = set(json.loads(line["labels"]))
            if line_labels.issubset(call_labels):
                conn.execute(
                    "UPDATE claim_cites SET valid = 1 WHERE claim_id = ? AND line_id = ?",
                    (cite["claim_id"], cite["line_id"])
                )
                valid_count += 1
            else:
                # Line exists but belongs to a different label set
                conn.execute(
                    "UPDATE claim_cites SET valid = 0 WHERE claim_id = ? AND line_id = ?",
                    (cite["claim_id"], cite["line_id"])
                )

    conn.commit()
    return valid_count, len(cites)


def assign_basis(claim_id: int, conn=None) -> str:
    """Assign basis: sourced if at least one valid cite, inferred otherwise.
    Returns the assigned basis.
    """
    if conn is None:
        conn = db.get_conn()

    valid = conn.execute(
        "SELECT COUNT(*) as cnt FROM claim_cites WHERE claim_id = ? AND valid = 1",
        (claim_id,)
    ).fetchone()["cnt"]

    basis = "sourced" if valid > 0 else "inferred"
    conn.execute("UPDATE claims SET basis = ? WHERE id = ?", (basis, claim_id))
    conn.commit()
    return basis


def compute_identity(claim_id: int, conn=None) -> str:
    """Compute claim identity: type + ':' + ','.join(sorted(valid_cite_ids)).
    Attested claims use 'attested:' + gap_id.
    """
    if conn is None:
        conn = db.get_conn()

    claim = conn.execute("SELECT type, source_gap_id FROM claims WHERE id = ?", (claim_id,)).fetchone()

    if claim["source_gap_id"]:
        identity = f"attested:{claim['source_gap_id']}"
    else:
        valid_cites = conn.execute(
            "SELECT line_id FROM claim_cites WHERE claim_id = ? AND valid = 1 ORDER BY line_id",
            (claim_id,)
        ).fetchall()
        cite_ids = ",".join(row["line_id"] for row in valid_cites)
        identity = f"{claim['type']}:{cite_ids}"

    conn.execute("UPDATE claims SET identity = ? WHERE id = ?", (identity, claim_id))
    conn.commit()
    return identity


def _normalise_tokens(text: str) -> set[str]:
    """Lowercase word tokens for subject overlap comparison."""
    if not text:
        return set()
    return set(re.findall(r'\w+', text.lower()))


def _normalise_value(value: str) -> str:
    """Normalise a value for conflict comparison:
    lowercase, strip currency symbols and thousands separators, keep digits.
    Compare dates as ISO.
    """
    if not value:
        return ""
    v = value.lower().strip()
    # Strip currency symbols and thousands separators
    v = re.sub(r'[₹$€£¥,]', '', v)
    # Try to parse as date
    date_match = re.match(r'(\d{4}-\d{2}-\d{2})', v)
    if date_match:
        return date_match.group(1)
    return v.strip()


def _token_overlap_coefficient(a: set[str], b: set[str]) -> float:
    """Token overlap coefficient: |A∩B| / min(|A|, |B|)."""
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    return intersection / min(len(a), len(b))


def detect_supersession(run_id: int, case_id: int, conn=None):
    """Detect supersession within each label set.

    If claim B has supersedes info and an earlier claim A in the same label set
    has matching normalised subject/token overlap and attribute (and value equals
    old_value when given), set the supersession flags.
    """
    if conn is None:
        conn = db.get_conn()

    claims = conn.execute(
        "SELECT id, type, subject, attribute, value, labels, flags FROM claims WHERE run_id = ? AND case_id = ?",
        (run_id, case_id)
    ).fetchall()

    # Build list of claims with supersedes info
    for claim in claims:
        flags = json.loads(claim["flags"])
        supersedes = flags.get("supersedes_info")
        if not supersedes:
            continue

        claim_labels = set(json.loads(claim["labels"]))
        claim_subject_tokens = _normalise_tokens(claim["subject"])

        # Find the claim being superseded
        for other in claims:
            if other["id"] == claim["id"]:
                continue

            other_labels = set(json.loads(other["labels"]))
            if other_labels != claim_labels:
                continue

            other_subject_tokens = _normalise_tokens(other["subject"])
            overlap = _token_overlap_coefficient(claim_subject_tokens, other_subject_tokens)

            # Check attribute match
            if claim["attribute"] != other["attribute"]:
                continue

            # Check subject overlap or exact supersedes.subject match
            supersedes_subject_tokens = _normalise_tokens(supersedes.get("subject", ""))
            other_match = (
                overlap >= 0.8 or
                _token_overlap_coefficient(supersedes_subject_tokens, other_subject_tokens) >= 0.8
            )

            if not other_match:
                continue

            # Check old_value if provided
            if supersedes.get("old_value"):
                if _normalise_value(other["value"]) != _normalise_value(supersedes["old_value"]):
                    continue

            # Set supersession flags
            other_flags = json.loads(other["flags"])
            other_flags["superseded_by"] = claim["id"]
            conn.execute("UPDATE claims SET flags = ? WHERE id = ?",
                         (json.dumps(other_flags), other["id"]))

            flags["supersedes"] = other["id"]
            conn.execute("UPDATE claims SET flags = ? WHERE id = ?",
                         (json.dumps(flags), claim["id"]))

    conn.commit()


def detect_conflicts(run_id: int, case_id: int, conn=None):
    """Detect conflicts across ALL label sets.

    Two claims conflict when:
    - attribute != 'none' and value is not null for both
    - attributes are equal
    - subject token overlap coefficient >= 0.8 (definite) or 0.5-0.8 (possible)
    - normalised values differ
    - Neither is superseded

    A conflict's label set is the union of its two claims' sets.
    """
    if conn is None:
        conn = db.get_conn()

    claims = conn.execute(
        """SELECT id, type, subject, attribute, value, labels, flags
           FROM claims WHERE run_id = ? AND case_id = ?
           AND attribute IS NOT NULL AND attribute != 'none'
           AND value IS NOT NULL AND value != ''""",
        (run_id, case_id)
    ).fetchall()

    checked = set()
    for i, a in enumerate(claims):
        a_flags = json.loads(a["flags"])
        if a_flags.get("superseded_by"):
            continue

        for j, b in enumerate(claims):
            if j <= i:
                continue

            b_flags = json.loads(b["flags"])
            if b_flags.get("superseded_by"):
                continue

            pair = (min(a["id"], b["id"]), max(a["id"], b["id"]))
            if pair in checked:
                continue
            checked.add(pair)

            # Attributes must match
            if a["attribute"] != b["attribute"]:
                continue

            # Subject overlap
            a_tokens = _normalise_tokens(a["subject"])
            b_tokens = _normalise_tokens(b["subject"])
            overlap = _token_overlap_coefficient(a_tokens, b_tokens)

            if overlap < 0.5:
                continue

            # Values must differ
            a_val = _normalise_value(a["value"])
            b_val = _normalise_value(b["value"])
            if a_val == b_val:
                continue

            possible = overlap < 0.8  # 0.5 to 0.8 is possible

            # Conflict label set is union of both claims' label sets
            conflict_labels = sorted(
                set(json.loads(a["labels"])) | set(json.loads(b["labels"]))
            )

            conflict_id = db.insert_conflict(
                case_id, run_id, a["id"], b["id"], conflict_labels, possible
            )

            # Update flags on both claims
            a_flags.setdefault("conflict_with", []).append(b["id"])
            if possible:
                a_flags["possible"] = True
            conn.execute("UPDATE claims SET flags = ? WHERE id = ?",
                         (json.dumps(a_flags), a["id"]))

            b_flags.setdefault("conflict_with", []).append(a["id"])
            if possible:
                b_flags["possible"] = True
            conn.execute("UPDATE claims SET flags = ? WHERE id = ?",
                         (json.dumps(b_flags), b["id"]))

    conn.commit()


def generate_deterministic_gaps(case_id: int, run_id: int, conn=None) -> list[int]:
    """Generate deterministic gaps from the task CSV: any row with an empty
    assignee that is not Done or Cancelled becomes a gap.

    If a model gap already cites the same line, merge by skipping the
    deterministic gap.

    Returns list of created gap IDs.
    """
    if conn is None:
        conn = db.get_conn()

    # Find the CSV source
    csv_source = conn.execute(
        "SELECT id FROM sources WHERE case_id = ? AND kind = 'csv'", (case_id,)
    ).fetchone()

    if not csv_source:
        return []

    source_id = csv_source["id"]
    source_labels = conn.execute(
        "SELECT labels FROM sources WHERE id = ?", (source_id,)
    ).fetchone()
    labels = json.loads(source_labels["labels"])

    # Get all lines from this CSV source
    csv_lines = conn.execute(
        "SELECT line_id, text FROM lines WHERE source_id = ?", (source_id,)
    ).fetchall()

    # Find existing model gap cite line_ids for this run
    existing_gap_lines = set()
    existing_gaps = conn.execute(
        "SELECT g.id FROM gaps g WHERE g.case_id = ? AND g.run_id = ?",
        (case_id, run_id)
    ).fetchall()
    for g in existing_gaps:
        cites = conn.execute(
            "SELECT line_id FROM gap_cites WHERE gap_id = ?", (g["id"],)
        ).fetchall()
        for c in cites:
            existing_gap_lines.add(c["line_id"])

    gap_ids = []
    for line in csv_lines:
        text = line["text"]
        # Parse the pipe-separated key=value format
        fields = {}
        for part in text.split(" | "):
            if "=" in part:
                k, v = part.split("=", 1)
                fields[k.strip()] = v.strip()

        task_id = fields.get("id", "")
        title = fields.get("title", "")
        assignee = fields.get("assignee", "")
        status = fields.get("status", "")

        if assignee == "" and status not in ("Done", "Cancelled"):
            # Check if a model gap already cites this line
            if line["line_id"] in existing_gap_lines:
                continue

            question = f"Who owns {task_id}: {title}?"
            gap_id = db.insert_gap(case_id, run_id, {
                "question": question,
                "why": f"Task {task_id} has no assignee and is not Done or Cancelled.",
                "labels": labels,
                "cites": [line["line_id"]],
                "status": "open",
            })
            gap_ids.append(gap_id)

    return gap_ids


def validate_gap_cites(gap_id: int, call_labels: set[str], conn=None) -> int:
    """Validate cites for a gap. Returns valid count."""
    if conn is None:
        conn = db.get_conn()

    cites = conn.execute(
        "SELECT gap_id, line_id FROM gap_cites WHERE gap_id = ?", (gap_id,)
    ).fetchall()

    valid_count = 0
    for cite in cites:
        line = conn.execute(
            "SELECT line_id, labels FROM lines WHERE line_id = ?",
            (cite["line_id"],)
        ).fetchone()

        if line is not None:
            line_labels = set(json.loads(line["labels"]))
            if line_labels.issubset(call_labels):
                conn.execute(
                    "UPDATE gap_cites SET valid = 1 WHERE gap_id = ? AND line_id = ?",
                    (cite["gap_id"], cite["line_id"])
                )
                valid_count += 1
            else:
                conn.execute(
                    "UPDATE gap_cites SET valid = 0 WHERE gap_id = ? AND line_id = ?",
                    (cite["gap_id"], cite["line_id"])
                )
        else:
            conn.execute(
                "UPDATE gap_cites SET valid = 0 WHERE gap_id = ? AND line_id = ?",
                (cite["gap_id"], cite["line_id"])
            )

    conn.commit()
    return valid_count


def run_verification_pipeline(case_id: int, run_id: int, call_label_sets: dict[str, set[str]],
                              conn=None):
    """Run the full verification pipeline on a completed extraction run.

    call_label_sets maps label-set key (e.g. "team" or "finance") to the
    set of labels used in that call.
    """
    if conn is None:
        conn = db.get_conn()

    # 1. Validate cites for all claims in this run
    claims = conn.execute(
        "SELECT id, labels FROM claims WHERE run_id = ? AND case_id = ?",
        (run_id, case_id)
    ).fetchall()

    for claim in claims:
        claim_labels = set(json.loads(claim["labels"]))
        # Find which call label set this claim belongs to
        call_labels = claim_labels  # Default: use claim's own labels
        for ls_key, ls_set in call_label_sets.items():
            if claim_labels.issubset(ls_set) or claim_labels == ls_set:
                call_labels = ls_set
                break

        validate_cites(claim["id"], call_labels, conn)
        assign_basis(claim["id"], conn)
        compute_identity(claim["id"], conn)

    # 2. Detect supersession
    detect_supersession(run_id, case_id, conn)

    # 3. Detect conflicts across all label sets
    detect_conflicts(run_id, case_id, conn)

    # 4. Validate gap cites
    gaps = conn.execute(
        "SELECT id, labels FROM gaps WHERE run_id = ? AND case_id = ?",
        (run_id, case_id)
    ).fetchall()

    for gap in gaps:
        gap_labels = set(json.loads(gap["labels"]))
        call_labels = gap_labels
        for ls_key, ls_set in call_label_sets.items():
            if gap_labels.issubset(ls_set) or gap_labels == ls_set:
                call_labels = ls_set
                break
        valid_count = validate_gap_cites(gap["id"], call_labels, conn)
        # Drop gaps with no valid cites
        if valid_count == 0:
            conn.execute("DELETE FROM gap_cites WHERE gap_id = ?", (gap["id"],))
            conn.execute("DELETE FROM gaps WHERE id = ?", (gap["id"],))

    conn.commit()

    # 5. Generate deterministic gaps from CSV
    generate_deterministic_gaps(case_id, run_id, conn)
