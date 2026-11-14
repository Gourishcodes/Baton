"""Persona-filtered repository queries for Baton.

Every function takes a persona and filters through access.visible().
Templates never filter. Routes never filter. This is the only data layer.
"""
import json

from . import db, access


def get_case(case_id: int, persona: str = None):
    """Get case metadata."""
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    return dict(row) if row else None


def get_sources(case_id: int, persona: str):
    """Get sources visible to the persona."""
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM sources WHERE case_id = ? ORDER BY id", (case_id,)
    ).fetchall()
    result = []
    for row in rows:
        labels = json.loads(row["labels"])
        if access.visible(labels, persona):
            d = dict(row)
            d["labels"] = labels
            d["line_count"] = conn.execute(
                "SELECT COUNT(*) as cnt FROM lines WHERE source_id = ?",
                (row["id"],)
            ).fetchone()["cnt"]
            result.append(d)
    return result


def get_all_sources(case_id: int):
    """Get all sources (for internal use, e.g. verification)."""
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM sources WHERE case_id = ? ORDER BY id", (case_id,)
    ).fetchall()
    return [dict(row) | {"labels": json.loads(row["labels"])} for row in rows]


def get_claims(case_id: int, persona: str, type_filter: str = None,
               needs_review: bool = False, run_id: int = None):
    """Get claims with visibility info for the persona.

    Returns a list of dicts. Each dict has a 'visible' key indicating whether
    the persona can see the full content. Hidden claims are returned as stubs.
    """
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

    result = []
    for row in rows:
        labels = json.loads(row["labels"])
        is_visible = access.visible(labels, persona)

        if is_visible:
            d = dict(row)
            d["labels"] = labels
            d["flags"] = json.loads(row["flags"])
            d["visible"] = True

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
            result.append({
                "id": row["id"],
                "visible": False,
                "labels": labels,
            })

    return result


def get_claim(case_id: int, claim_id: int, persona: str):
    """Get a single claim. Returns None if not found or hidden."""
    conn = db.get_conn()
    row = conn.execute(
        "SELECT * FROM claims WHERE id = ? AND case_id = ?",
        (claim_id, case_id)
    ).fetchone()
    if not row:
        return None

    labels = json.loads(row["labels"])
    if not access.visible(labels, persona):
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

    return d


def get_gaps(case_id: int, persona: str):
    """Get gaps visible to the persona."""
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM gaps WHERE case_id = ? ORDER BY id", (case_id,)
    ).fetchall()

    result = []
    for row in rows:
        labels = json.loads(row["labels"])
        is_visible = access.visible(labels, persona)

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

            # Get cited line texts for display
            d["cited_lines"] = []
            for cite in d["cites"]:
                if cite["valid"]:
                    line = conn.execute(
                        "SELECT text FROM lines WHERE line_id = ?",
                        (cite["line_id"],)
                    ).fetchone()
                    if line:
                        d["cited_lines"].append({
                            "line_id": cite["line_id"],
                            "text": line["text"]
                        })

            # Get answer claim if answered
            if row["answer_claim_id"]:
                answer = get_claim(case_id, row["answer_claim_id"], persona)
                d["answer_claim"] = answer
            else:
                d["answer_claim"] = None

            result.append(d)
        else:
            result.append({
                "id": row["id"],
                "visible": False,
                "labels": labels,
            })

    return result


def get_gap(case_id: int, gap_id: int, persona: str):
    """Get a single gap. Returns None if not found or hidden."""
    conn = db.get_conn()
    row = conn.execute(
        "SELECT * FROM gaps WHERE id = ? AND case_id = ?",
        (gap_id, case_id)
    ).fetchone()
    if not row:
        return None

    labels = json.loads(row["labels"])
    if not access.visible(labels, persona):
        return None

    d = dict(row)
    d["labels"] = labels
    d["visible"] = True
    return d


def get_conflicts(case_id: int, persona: str):
    """Get conflicts visible to the persona."""
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM conflicts WHERE case_id = ? ORDER BY id", (case_id,)
    ).fetchall()

    result = []
    for row in rows:
        labels = json.loads(row["labels"])
        is_visible = access.visible(labels, persona)

        if is_visible:
            d = dict(row)
            d["labels"] = labels
            d["visible"] = True

            # Get both claims
            d["claim_a_data"] = get_claim(case_id, row["claim_a"], persona)
            d["claim_b_data"] = get_claim(case_id, row["claim_b"], persona)
            result.append(d)
        else:
            result.append({
                "id": row["id"],
                "visible": False,
                "labels": labels,
            })

    return result


def get_lines_for_source(source_id: str, persona: str):
    """Get all lines for a source, checking visibility."""
    conn = db.get_conn()

    source = conn.execute(
        "SELECT * FROM sources WHERE id = ?", (source_id,)
    ).fetchone()
    if not source:
        return None, None

    labels = json.loads(source["labels"])
    if not access.visible(labels, persona):
        return None, None

    lines = conn.execute(
        "SELECT * FROM lines WHERE source_id = ? ORDER BY n", (source_id,)
    ).fetchall()
    return dict(source) | {"labels": labels}, [dict(l) for l in lines]


def get_line(line_id: str, persona: str):
    """Get a single line, checking visibility."""
    conn = db.get_conn()
    line = conn.execute("SELECT * FROM lines WHERE line_id = ?", (line_id,)).fetchone()
    if not line:
        return None

    labels = json.loads(line["labels"])
    if not access.visible(labels, persona):
        return None

    return dict(line) | {"labels": labels}


def get_versions(case_id: int, persona: str):
    """Get version history."""
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM versions WHERE case_id = ? ORDER BY n DESC", (case_id,)
    ).fetchall()
    return [dict(row) for row in rows]


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
