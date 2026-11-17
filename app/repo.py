"""Principal-filtered repository queries for Baton.

Every function takes a Principal and filters through access.visible().
Templates never filter. Routes never filter. This is the only data layer.
"""
import json

from . import db, access
from .auth import Principal


def _check_principal(principal: Principal):
    if not isinstance(principal, Principal):
        raise TypeError(f"Expected Principal, got {type(principal).__name__}")


def get_case(case_id: int, principal: Principal):
    """Get case metadata."""
    _check_principal(principal)
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    return dict(row) if row else None


def get_sources(case_id: int, principal: Principal):
    """Get sources visible to the principal, scoped by case_id."""
    _check_principal(principal)
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM sources WHERE case_id = ? ORDER BY id", (case_id,)
    ).fetchall()
    result = []
    for row in rows:
        labels = json.loads(row["labels"])
        if access.visible(labels, principal):
            d = dict(row)
            d["labels"] = labels
            d["line_count"] = conn.execute(
                """SELECT COUNT(*) as cnt FROM lines l
                   JOIN sources s ON l.source_id = s.id
                   WHERE l.source_id = ? AND s.case_id = ?""",
                (row["id"], case_id)
            ).fetchone()["cnt"]
            result.append(d)
    return result


def get_all_sources(case_id: int, principal: Principal):
    """Get all sources (for internal use, e.g. verification)."""
    _check_principal(principal)
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM sources WHERE case_id = ? ORDER BY id", (case_id,)
    ).fetchall()
    return [dict(row) | {"labels": json.loads(row["labels"])} for row in rows]


def get_claims(case_id: int, principal: Principal, type_filter: str = None,
               needs_review: bool = False, run_id: int = None):
    """Get claims with visibility info for the principal, scoped by case_id.

    Returns a list of dicts. Each dict has a 'visible' key indicating whether
    the principal can see the full content. Hidden claims are returned as stubs.
    """
    _check_principal(principal)
    conn = db.get_conn()

    query = "SELECT * FROM claims WHERE case_id = ?"
    params = [case_id]

    if run_id is not None:
        query += " AND run_id = ?"
        params.append(run_id)

    if type_filter:
        query += " AND type = ?"
        params.append(type_filter)

    query += " ORDER BY id"
    rows = conn.execute(query, params).fetchall()

    first_cites_rows = conn.execute(
        """SELECT cc.claim_id, cc.line_id
           FROM claim_cites cc
           JOIN claims c ON cc.claim_id = c.id
           WHERE c.case_id = ?
           ORDER BY cc.claim_id, cc.rowid""",
        (case_id,)
    ).fetchall()
    first_cite_by_claim = {}
    for r in first_cites_rows:
        cid = r["claim_id"]
        if cid not in first_cite_by_claim:
            first_cite_by_claim[cid] = r["line_id"].replace(":", "·")

    result = []
    for row in rows:
        labels = json.loads(row["labels"])
        is_visible = access.visible(labels, principal)

        if is_visible:
            d = dict(row)
            d["labels"] = labels
            d["flags"] = json.loads(row["flags"])
            d["visible"] = True

            conflict_with = d["flags"].get("conflict_with")
            if conflict_with and isinstance(conflict_with, list) and len(conflict_with) > 0:
                other_cid = conflict_with[0]
                d["conflict_cite"] = first_cite_by_claim.get(other_cid, "")

            superseded_by = d["flags"].get("superseded_by")
            if superseded_by:
                d["superseded_by_cite"] = first_cite_by_claim.get(superseded_by, "")

            supersedes = d["flags"].get("supersedes")
            if supersedes:
                d["supersedes_cite"] = first_cite_by_claim.get(supersedes, "")

            # Get cites
            cites = conn.execute(
                "SELECT line_id, valid FROM claim_cites WHERE claim_id = ?",
                (row["id"],)
            ).fetchall()
            d["cites"] = [{"line_id": c["line_id"], "valid": bool(c["valid"])} for c in cites]

            # Get decision status
            if row["identity"]:
                decision = conn.execute(
                    "SELECT * FROM decisions WHERE identity = ? AND case_id = ?",
                    (row["identity"], case_id)
                ).fetchone()
                d["decision"] = dict(decision) if decision else None
            else:
                d["decision"] = None

            if needs_review and d["decision"] is not None:
                continue

            result.append(d)
        else:
            dec = None
            if row["identity"]:
                d_row = conn.execute(
                    "SELECT decision FROM decisions WHERE identity = ? AND case_id = ?",
                    (row["identity"], case_id)
                ).fetchone()
                if d_row:
                    dec = {"decision": d_row["decision"]}
            result.append({
                "id": row["id"],
                "type": row["type"],
                "visible": False,
                "labels": labels,
                "decision": dec,
                "case_id": case_id,
            })

    return result


def get_claim(case_id: int, claim_id: int, principal: Principal):
    """Get a single claim scoped by case_id. Returns None if not found or hidden."""
    _check_principal(principal)
    conn = db.get_conn()
    row = conn.execute(
        "SELECT * FROM claims WHERE id = ? AND case_id = ?",
        (claim_id, case_id)
    ).fetchone()
    if not row:
        return None

    labels = json.loads(row["labels"])
    if not access.visible(labels, principal):
        return None

    d = dict(row)
    d["labels"] = labels
    d["flags"] = json.loads(row["flags"])
    d["visible"] = True

    cites = conn.execute(
        "SELECT line_id, valid FROM claim_cites WHERE claim_id = ?",
        (claim_id,)
    ).fetchall()
    d["cites"] = [{"line_id": c["line_id"], "valid": bool(c["valid"])} for c in cites]

    if row["identity"]:
        decision = conn.execute(
            "SELECT * FROM decisions WHERE identity = ? AND case_id = ?",
            (row["identity"], case_id)
        ).fetchone()
        d["decision"] = dict(decision) if decision else None
    else:
        d["decision"] = None

    conflict_with = d["flags"].get("conflict_with")
    if conflict_with and isinstance(conflict_with, list) and len(conflict_with) > 0:
        other_cid = conflict_with[0]
        other_cite = conn.execute(
            """SELECT cc.line_id FROM claim_cites cc
               JOIN claims c ON cc.claim_id = c.id
               WHERE cc.claim_id = ? AND c.case_id = ?
               ORDER BY cc.rowid LIMIT 1""",
            (other_cid, case_id)
        ).fetchone()
        d["conflict_cite"] = other_cite["line_id"].replace(":", "·") if other_cite else ""

    superseded_by = d["flags"].get("superseded_by")
    if superseded_by:
        other_cite = conn.execute(
            """SELECT cc.line_id FROM claim_cites cc
               JOIN claims c ON cc.claim_id = c.id
               WHERE cc.claim_id = ? AND c.case_id = ?
               ORDER BY cc.rowid LIMIT 1""",
            (superseded_by, case_id)
        ).fetchone()
        d["superseded_by_cite"] = other_cite["line_id"].replace(":", "·") if other_cite else ""

    supersedes = d["flags"].get("supersedes")
    if supersedes:
        other_cite = conn.execute(
            """SELECT cc.line_id FROM claim_cites cc
               JOIN claims c ON cc.claim_id = c.id
               WHERE cc.claim_id = ? AND c.case_id = ?
               ORDER BY cc.rowid LIMIT 1""",
            (supersedes, case_id)
        ).fetchone()
        d["supersedes_cite"] = other_cite["line_id"].replace(":", "·") if other_cite else ""

    return d


def get_gaps(case_id: int, principal: Principal):
    """Get gaps visible to the principal, scoped by case_id."""
    _check_principal(principal)
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM gaps WHERE case_id = ? ORDER BY id", (case_id,)
    ).fetchall()

    result = []
    for row in rows:
        labels = json.loads(row["labels"])
        is_visible = access.visible(labels, principal)

        if is_visible:
            d = dict(row)
            d["labels"] = labels
            d["visible"] = True

            # Get cites
            cites = conn.execute(
                "SELECT line_id, valid FROM gap_cites WHERE gap_id = ?",
                (row["id"],)
            ).fetchall()
            d["cites"] = [{"line_id": c["line_id"], "valid": bool(c["valid"])} for c in cites]

            # Get cited line texts for display, scoped to this case
            d["cited_lines"] = []
            for cite in d["cites"]:
                if cite["valid"]:
                    line = conn.execute(
                        """SELECT l.text FROM lines l
                           JOIN sources s ON l.source_id = s.id
                           WHERE l.line_id = ? AND s.case_id = ?""",
                        (cite["line_id"], case_id)
                    ).fetchone()
                    if line:
                        d["cited_lines"].append({
                            "line_id": cite["line_id"],
                            "text": line["text"]
                        })

            # Get answer claim if answered
            if row["answer_claim_id"]:
                answer = get_claim(case_id, row["answer_claim_id"], principal)
                d["answer_claim"] = answer
            else:
                d["answer_claim"] = None

            result.append(d)

    return result


def get_gap(case_id: int, gap_id: int, principal: Principal):
    """Get a single gap scoped by case_id. Returns None if not found or hidden."""
    _check_principal(principal)
    conn = db.get_conn()
    row = conn.execute(
        "SELECT * FROM gaps WHERE id = ? AND case_id = ?",
        (gap_id, case_id)
    ).fetchone()
    if not row:
        return None

    labels = json.loads(row["labels"])
    if not access.visible(labels, principal):
        return None

    d = dict(row)
    d["labels"] = labels
    d["visible"] = True
    return d


def get_conflicts(case_id: int, principal: Principal):
    """Get conflicts visible to the principal, scoped by case_id."""
    _check_principal(principal)
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM conflicts WHERE case_id = ? ORDER BY id", (case_id,)
    ).fetchall()

    result = []
    for row in rows:
        labels = json.loads(row["labels"])
        is_visible = access.visible(labels, principal)

        if is_visible:
            d = dict(row)
            d["labels"] = labels
            d["visible"] = True

            # Get both claims
            d["claim_a_data"] = get_claim(case_id, row["claim_a"], principal)
            d["claim_b_data"] = get_claim(case_id, row["claim_b"], principal)
            result.append(d)

    return result


def get_conflicts_count(case_id: int, principal: Principal) -> int:
    """Get total conflict count for a case."""
    _check_principal(principal)
    conn = db.get_conn()
    row = conn.execute("SELECT COUNT(*) as cnt FROM conflicts WHERE case_id = ?", (case_id,)).fetchone()
    return row["cnt"] if row else 0


def get_lines_for_source(case_id: int, source_id: str, principal: Principal):
    """Get all lines for a source scoped by case_id, checking visibility."""
    _check_principal(principal)
    conn = db.get_conn()

    source = conn.execute(
        "SELECT * FROM sources WHERE id = ? AND case_id = ?", (source_id, case_id)
    ).fetchone()
    if not source:
        return None, None

    labels = json.loads(source["labels"])
    if not access.visible(labels, principal):
        return None, None

    lines = conn.execute(
        """SELECT l.* FROM lines l
           JOIN sources s ON l.source_id = s.id
           WHERE l.source_id = ? AND s.case_id = ?
           ORDER BY l.n""",
        (source_id, case_id)
    ).fetchall()
    return dict(source) | {"labels": labels}, [dict(l) for l in lines]


def get_line(case_id: int, line_id: str, principal: Principal):
    """Get a single line scoped by case_id, checking visibility."""
    _check_principal(principal)
    conn = db.get_conn()
    line = conn.execute(
        """SELECT l.* FROM lines l
           JOIN sources s ON l.source_id = s.id
           WHERE l.line_id = ? AND s.case_id = ?""",
        (line_id, case_id)
    ).fetchone()
    if not line:
        return None

    labels = json.loads(line["labels"])
    if not access.visible(labels, principal):
        return None

    return dict(line) | {"labels": labels}


def get_versions(case_id: int, principal: Principal):
    """Get version history scoped by case_id."""
    _check_principal(principal)
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM versions WHERE case_id = ? ORDER BY n DESC", (case_id,)
    ).fetchall()
    return [dict(row) for row in rows]


def get_version(case_id: int, version_n: int, principal: Principal):
    """Get a specific version snapshot scoped by case_id."""
    _check_principal(principal)
    conn = db.get_conn()
    row = conn.execute(
        "SELECT * FROM versions WHERE case_id = ? AND n = ?", (case_id, version_n)
    ).fetchone()
    return dict(row) if row else None


def get_latest_version(case_id: int) -> int:
    """Get the latest version number, or 0 if none."""
    conn = db.get_conn()
    row = conn.execute(
        "SELECT MAX(n) as max_n FROM versions WHERE case_id = ?", (case_id,)
    ).fetchone()
    return row["max_n"] or 0


def get_acknowledgement(case_id: int, version_n: int):
    """Get acknowledgement for a version."""
    conn = db.get_conn()
    row = conn.execute(
        "SELECT * FROM acknowledgements WHERE case_id = ? AND version_n = ?",
        (case_id, version_n)
    ).fetchone()
    return dict(row) if row else None


def get_latest_run(case_id: int):
    """Get the latest completed run."""
    conn = db.get_conn()
    row = conn.execute(
        "SELECT * FROM runs WHERE case_id = ? AND status = 'done' ORDER BY id DESC LIMIT 1",
        (case_id,)
    ).fetchone()
    return dict(row) if row else None


def get_dropped_claims(case_id: int, principal: Principal):
    """Get dropped claims recorded during verification."""
    _check_principal(principal)
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM dropped_claims WHERE case_id = ? ORDER BY id",
        (case_id,)
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["cites"] = json.loads(r["cites"])
        result.append(d)
    return result


def answer_gap(case_id: int, gap_id: int, text: str, claim_type: str, label: str, principal: Principal) -> int:
    """Answer a gap creating an attested claim, scoped to case_id."""
    _check_principal(principal)
    conn = db.get_conn()
    run = get_latest_run(case_id)
    run_id = run["id"] if run else 1

    cur = conn.execute(
        """INSERT INTO claims (case_id, run_id, type, text, basis, labels, flags, identity, attested_by, attested_at, source_gap_id)
           VALUES (?, ?, ?, ?, 'attested', ?, '{}', ?, ?, ?, ?)""",
        (case_id, run_id, claim_type, text, json.dumps([label]), f"attested:{gap_id}", principal.name, db.now_iso(), gap_id)
    )
    claim_id = cur.lastrowid
    conn.execute(
        "UPDATE gaps SET status = 'answered', answer_claim_id = ? WHERE id = ? AND case_id = ?",
        (claim_id, gap_id, case_id)
    )
    conn.commit()
    return claim_id


def close_case(case_id: int, principal: Principal):
    """Close a case upon acknowledgement."""
    _check_principal(principal)
    conn = db.get_conn()
    conn.execute("UPDATE cases SET status = 'closed' WHERE id = ?", (case_id,))
    conn.commit()


def reset_demo_case(case_id: int = 1):
    """Reset the demo case back to initial seeded state, touching case_id ONLY."""
    conn = db.get_conn()
    conn.execute("DELETE FROM decisions WHERE case_id = ?", (case_id,))
    conn.execute("DELETE FROM versions WHERE case_id = ?", (case_id,))
    conn.execute("DELETE FROM acknowledgements WHERE case_id = ?", (case_id,))
    conn.execute("DELETE FROM dropped_claims WHERE case_id = ?", (case_id,))
    conn.execute("UPDATE gaps SET status = 'open', answer_claim_id = NULL WHERE case_id = ?", (case_id,))
    conn.execute(
        "DELETE FROM claim_cites WHERE claim_id IN (SELECT id FROM claims WHERE case_id = ? AND basis = 'attested')",
        (case_id,)
    )
    conn.execute("DELETE FROM claims WHERE case_id = ? AND basis = 'attested'", (case_id,))
    conn.execute("UPDATE cases SET status = 'open' WHERE id = ?", (case_id,))
    conn.commit()
