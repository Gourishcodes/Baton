"""Review logic for Baton: claim identity, sticky decisions, version snapshots.

Implements SPEC section 8.
"""
import json

from . import db


def compute_jaccard(set_a: set, set_b: set) -> float:
    """Jaccard similarity of two sets."""
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def get_valid_cite_ids(claim_id: int, conn=None) -> set[str]:
    """Get the set of valid cite line IDs for a claim."""
    if conn is None:
        conn = db.get_conn()
    rows = conn.execute(
        "SELECT line_id FROM claim_cites WHERE claim_id = ? AND valid = 1",
        (claim_id,)
    ).fetchall()
    return {row["line_id"] for row in rows}


def match_prior_decisions(case_id: int, run_id: int, conn=None):
    """After a run finishes, for each new claim find prior decisions
    and inherit or flag them.

    1. Exact identity match: inherit the decision.
    2. Same type and Jaccard of cite-ID sets >= 0.6: inherit.
    3. Rejected decision with same type and Jaccard 0.34 to 0.6:
       do not inherit; set possibly_previously_rejected flag.
    4. Edited decision keeps its edited text and is locked.
    """
    if conn is None:
        conn = db.get_conn()

    # Get all prior decisions for this case
    prior_decisions = conn.execute(
        "SELECT * FROM decisions WHERE case_id = ?", (case_id,)
    ).fetchall()

    if not prior_decisions:
        return

    # Build a lookup of prior decisions with their cite sets
    # We need the claims from previous runs to get cite sets for Jaccard
    prior_claims_by_identity = {}
    for dec in prior_decisions:
        identity = dec["identity"]
        prior_claims_by_identity[identity] = dict(dec)

    # Get all claims from THIS run
    new_claims = conn.execute(
        "SELECT id, type, identity, flags FROM claims WHERE run_id = ? AND case_id = ?",
        (run_id, case_id)
    ).fetchall()

    for claim in new_claims:
        if not claim["identity"]:
            continue

        flags = json.loads(claim["flags"])
        identity = claim["identity"]
        claim_type = claim["type"]
        cite_ids = get_valid_cite_ids(claim["id"], conn)

        matched = False

        # 1. Exact identity match
        if identity in prior_claims_by_identity:
            dec = prior_claims_by_identity[identity]
            _apply_decision(claim["id"], dec, flags, conn)
            matched = True
            continue

        # 2 & 3. Jaccard-based matching
        for dec in prior_decisions:
            prior_identity = dec["identity"]
            # Parse the type from the identity (format: "type:cite1,cite2")
            if ":" not in prior_identity:
                continue
            prior_type = prior_identity.split(":")[0]

            if prior_type != claim_type:
                continue

            # Get prior cite IDs from the identity string
            prior_cite_str = prior_identity.split(":", 1)[1]
            prior_cite_ids = set(prior_cite_str.split(",")) if prior_cite_str else set()

            jaccard = compute_jaccard(cite_ids, prior_cite_ids)

            if jaccard >= 0.6:
                # Inherit the decision
                _apply_decision(claim["id"], dict(dec), flags, conn)
                matched = True
                break
            elif 0.34 <= jaccard < 0.6 and dec["decision"] == "rejected":
                # Do not inherit; flag as possibly previously rejected
                flags["possibly_previously_rejected"] = True
                conn.execute("UPDATE claims SET flags = ? WHERE id = ?",
                             (json.dumps(flags), claim["id"]))
                matched = True
                break

    conn.commit()


def _apply_decision(claim_id: int, dec: dict, flags: dict, conn):
    """Apply a prior decision to a claim."""
    identity = conn.execute(
        "SELECT identity FROM claims WHERE id = ?", (claim_id,)
    ).fetchone()["identity"]

    # Write a new decision row for this claim's identity
    conn.execute(
        """INSERT OR REPLACE INTO decisions (identity, case_id, decision, edited_text, by, at, seeded)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (identity, dec["case_id"], dec["decision"], dec.get("edited_text"),
         dec["by"], dec["at"], dec.get("seeded", 0))
    )


def record_decision(case_id: int, identity: str, decision: str,
                     edited_text: str = None, by: str = None):
    """Record a review decision (approve, edit, reject, restore)."""
    conn = db.get_conn()

    at = db.now_iso()

    if decision == "restore":
        # Restoring = deleting the decision so it becomes pending again
        conn.execute(
            "DELETE FROM decisions WHERE identity = ? AND case_id = ?",
            (identity, case_id)
        )
    else:
        conn.execute(
            """INSERT OR REPLACE INTO decisions (identity, case_id, decision, edited_text, by, at, seeded)
               VALUES (?, ?, ?, ?, ?, ?, 0)""",
            (identity, case_id, decision, edited_text, by, at)
        )

    conn.commit()


def get_decision(case_id: int, identity: str):
    """Get the current decision for a claim identity."""
    conn = db.get_conn()
    row = conn.execute(
        "SELECT * FROM decisions WHERE identity = ? AND case_id = ?",
        (identity, case_id)
    ).fetchone()
    return dict(row) if row else None


def is_decision_locked(case_id: int, identity: str) -> bool:
    """An edited decision is locked - the text can't be changed by re-runs."""
    dec = get_decision(case_id, identity)
    return dec is not None and dec["decision"] == "edited"


def create_version(case_id: int, approved_by: str, seeded: bool = False) -> int:
    """Create a new handover version snapshot."""
    conn = db.get_conn()
    latest_n = conn.execute(
        "SELECT COALESCE(MAX(n), 0) as n FROM versions WHERE case_id = ?",
        (case_id,)
    ).fetchone()["n"]

    new_n = latest_n + 1

    # Snapshot: all approved/edited claims
    claims = conn.execute(
        """SELECT c.id, c.text, c.identity, d.decision
           FROM claims c JOIN decisions d ON c.identity = d.identity AND c.case_id = d.case_id
           WHERE c.case_id = ? AND d.decision IN ('approved', 'edited')
           ORDER BY c.id""",
        (case_id,)
    ).fetchall()

    snapshot = [{"id": c["id"], "text": c["text"], "identity": c["identity"],
                 "decision": c["decision"]} for c in claims]

    conn.execute(
        "INSERT INTO versions (case_id, n, approved_by, approved_at, snapshot, seeded) VALUES (?, ?, ?, ?, ?, ?)",
        (case_id, new_n, approved_by, db.now_iso(), json.dumps(snapshot), int(seeded))
    )
    conn.commit()
    return new_n


def acknowledge_version(case_id: int, version_n: int, by: str):
    """Acknowledge receipt of a handover version."""
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO acknowledgements (case_id, version_n, by, at) VALUES (?, ?, ?, ?)",
        (case_id, version_n, by, db.now_iso())
    )
    conn.commit()


def seed_demo_decisions(case_id: int, conn=None):
    """Seed demo state: mark every claim with basis=sourced, no conflict flag
    and no possibly_previously_rejected as approved by 'Divya Menon (seeded)'.
    """
    if conn is None:
        conn = db.get_conn()

    claims = conn.execute(
        "SELECT id, identity, basis, flags FROM claims WHERE case_id = ?",
        (case_id,)
    ).fetchall()

    for claim in claims:
        if not claim["identity"]:
            continue
        flags = json.loads(claim["flags"])
        if claim["basis"] == "sourced" and \
           not flags.get("conflict_with") and \
           not flags.get("possibly_previously_rejected"):
            conn.execute(
                """INSERT OR REPLACE INTO decisions (identity, case_id, decision, edited_text, by, at, seeded)
                   VALUES (?, ?, 'approved', NULL, 'Divya Menon (seeded)', ?, 1)""",
                (claim["identity"], case_id, db.now_iso())
            )

    conn.commit()
