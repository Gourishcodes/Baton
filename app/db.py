"""SQLite database schema and operations for Baton.

Uses stdlib sqlite3 only. No ORM.
"""
import json
import hashlib
import os
import sqlite3
import threading
from datetime import datetime, timezone

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    leaver TEXT,
    successor TEXT,
    approver TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    filename TEXT NOT NULL,
    kind TEXT NOT NULL,
    labels TEXT NOT NULL,  -- JSON array
    sha256 TEXT NOT NULL,
    uploaded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lines (
    line_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id),
    n INTEGER NOT NULL,
    text TEXT NOT NULL,
    hash TEXT NOT NULL,
    labels TEXT NOT NULL  -- JSON array
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    mode TEXT,
    provider TEXT,
    model TEXT,
    started_at TEXT,
    finished_at TEXT,
    status TEXT,
    note TEXT
);

CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    run_id INTEGER REFERENCES runs(id),
    type TEXT NOT NULL,
    text TEXT NOT NULL,
    subject TEXT,
    attribute TEXT,
    value TEXT,
    owner TEXT,
    due TEXT,
    event_date TEXT,
    basis TEXT NOT NULL,
    labels TEXT NOT NULL,  -- JSON array
    flags TEXT NOT NULL DEFAULT '{}',  -- JSON object
    identity TEXT,
    attested_by TEXT,
    attested_at TEXT,
    source_gap_id INTEGER REFERENCES gaps(id)
);

CREATE TABLE IF NOT EXISTS claim_cites (
    claim_id INTEGER NOT NULL REFERENCES claims(id),
    line_id TEXT NOT NULL,
    valid INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS gaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    run_id INTEGER REFERENCES runs(id),
    question TEXT NOT NULL,
    why TEXT,
    labels TEXT NOT NULL,  -- JSON array
    status TEXT NOT NULL DEFAULT 'open',
    answer_claim_id INTEGER REFERENCES claims(id)
);

CREATE TABLE IF NOT EXISTS gap_cites (
    gap_id INTEGER NOT NULL REFERENCES gaps(id),
    line_id TEXT NOT NULL,
    valid INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS conflicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    run_id INTEGER REFERENCES runs(id),
    claim_a INTEGER NOT NULL REFERENCES claims(id),
    claim_b INTEGER NOT NULL REFERENCES claims(id),
    labels TEXT NOT NULL,  -- JSON array
    possible INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS decisions (
    identity TEXT NOT NULL,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    decision TEXT NOT NULL,
    edited_text TEXT,
    by TEXT,
    at TEXT,
    seeded INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (identity, case_id)
);

CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    n INTEGER NOT NULL,
    approved_by TEXT,
    approved_at TEXT,
    snapshot TEXT NOT NULL,  -- JSON
    seeded INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS acknowledgements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    version_n INTEGER NOT NULL,
    by TEXT NOT NULL,
    at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    persona TEXT,
    action TEXT NOT NULL,
    target TEXT
);

CREATE TABLE IF NOT EXISTS dropped_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    run_id INTEGER REFERENCES runs(id),
    text TEXT NOT NULL,
    cites TEXT NOT NULL,  -- JSON array
    reason TEXT NOT NULL
);
"""

_local = threading.local()
_db_path: str | None = None


def get_db_path() -> str:
    global _db_path
    if _db_path is None:
        _db_path = os.environ.get("BATON_DB", "baton.db")
    return _db_path


def set_db_path(path: str):
    """Set the database path. Closes any existing connection."""
    global _db_path
    close()
    _db_path = path


def get_conn() -> sqlite3.Connection:
    """Get a database connection, creating the schema if needed."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(get_db_path(), check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
        _local.conn.executescript(SCHEMA)
    return _local.conn


def close():
    if hasattr(_local, "conn") and _local.conn is not None:
        try:
            _local.conn.close()
        except Exception:
            pass
        _local.conn = None


def reset():
    """Reset the module state (for testing)."""
    global _db_path
    close()
    _db_path = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def load_demo_case(data_dir: str = DATA) -> int:
    """Load the demo case from data/ into the database. Returns the case ID."""
    from . import parsers

    conn = get_conn()

    # Check if case already exists
    row = conn.execute("SELECT id FROM cases WHERE id = 1").fetchone()
    if row:
        return row["id"]

    roles_path = os.path.join(data_dir, "roles.json")
    with open(roles_path, encoding="utf-8") as f:
        roles_data = json.load(f)

    # Create the case
    conn.execute(
        "INSERT INTO cases (id, name, leaver, successor, approver, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (1, "Dispatch notifications", "Meera Joshi", "Arjun Rao", "Divya Menon", now_iso())
    )

    # Load sources and lines
    for src in roles_data["sources"]:
        filepath = os.path.join(data_dir, src["file"])
        sha = file_sha256(filepath)
        labels_json = json.dumps(sorted(src["labels"]))

        conn.execute(
            "INSERT INTO sources (id, case_id, filename, kind, labels, sha256, uploaded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (src["id"], 1, src["file"], src["kind"], labels_json, sha, now_iso())
        )

        lines = parsers.number_file(filepath, src["kind"], src["id"])
        for line in lines:
            conn.execute(
                "INSERT INTO lines (line_id, source_id, n, text, hash, labels) VALUES (?, ?, ?, ?, ?, ?)",
                (line["line_id"], src["id"], line["n"], line["text"], line["hash"], labels_json)
            )

    conn.commit()
    return 1


def insert_run(case_id: int, mode: str = "fixture", provider: str = "fixture",
               model: str = "fixture") -> int:
    """Insert a run record and return its ID."""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO runs (case_id, mode, provider, model, started_at, status) VALUES (?, ?, ?, ?, ?, ?)",
        (case_id, mode, provider, model, now_iso(), "running")
    )
    conn.commit()
    return cur.lastrowid


def finish_run(run_id: int, status: str = "done", note: str = None):
    conn = get_conn()
    conn.execute(
        "UPDATE runs SET finished_at = ?, status = ?, note = ? WHERE id = ?",
        (now_iso(), status, note, run_id)
    )
    conn.commit()


def insert_claim(case_id: int, run_id: int, claim_data: dict) -> int:
    """Insert a claim and its cites. Returns the claim ID."""
    conn = get_conn()
    flags = json.dumps(claim_data.get("flags", {}))
    labels = json.dumps(sorted(claim_data.get("labels", [])))

    cur = conn.execute(
        """INSERT INTO claims (case_id, run_id, type, text, subject, attribute, value,
           owner, due, event_date, basis, labels, flags, identity, attested_by, attested_at, source_gap_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (case_id, run_id, claim_data["type"], claim_data["text"],
         claim_data.get("subject"), claim_data.get("attribute"),
         claim_data.get("value"), claim_data.get("owner"),
         claim_data.get("due"), claim_data.get("event_date"),
         claim_data["basis"], labels, flags,
         claim_data.get("identity"), claim_data.get("attested_by"),
         claim_data.get("attested_at"), claim_data.get("source_gap_id"))
    )
    claim_id = cur.lastrowid

    # Insert cites
    for cite in claim_data.get("cites", []):
        valid = cite.get("valid", 1) if isinstance(cite, dict) else 1
        line_id = cite.get("line_id") if isinstance(cite, dict) else cite
        conn.execute(
            "INSERT INTO claim_cites (claim_id, line_id, valid) VALUES (?, ?, ?)",
            (claim_id, line_id, valid)
        )

    conn.commit()
    return claim_id


def insert_gap(case_id: int, run_id: int, gap_data: dict) -> int:
    """Insert a gap and its cites. Returns the gap ID."""
    conn = get_conn()
    labels = json.dumps(sorted(gap_data.get("labels", [])))

    cur = conn.execute(
        "INSERT INTO gaps (id, case_id, run_id, question, why, labels, status) VALUES (NULL, ?, ?, ?, ?, ?, ?)",
        (case_id, run_id, gap_data["question"], gap_data.get("why"),
         labels, gap_data.get("status", "open"))
    )
    gap_id = cur.lastrowid

    for cite in gap_data.get("cites", []):
        valid = cite.get("valid", 1) if isinstance(cite, dict) else 1
        line_id = cite.get("line_id") if isinstance(cite, dict) else cite
        conn.execute(
            "INSERT INTO gap_cites (gap_id, line_id, valid) VALUES (?, ?, ?)",
            (gap_id, line_id, valid)
        )

    conn.commit()
    return gap_id


def insert_conflict(case_id: int, run_id: int, claim_a: int, claim_b: int,
                    labels: list[str], possible: bool = False) -> int:
    """Insert a conflict record. Returns the conflict ID."""
    conn = get_conn()
    labels_json = json.dumps(sorted(labels))
    cur = conn.execute(
        "INSERT INTO conflicts (case_id, run_id, claim_a, claim_b, labels, possible) VALUES (?, ?, ?, ?, ?, ?)",
        (case_id, run_id, claim_a, claim_b, labels_json, int(possible))
    )
    conn.commit()
    return cur.lastrowid


def update_claim_flags(claim_id: int, flags: dict):
    """Update the flags JSON for a claim."""
    conn = get_conn()
    conn.execute("UPDATE claims SET flags = ? WHERE id = ?", (json.dumps(flags), claim_id))
    conn.commit()


def update_claim_identity(claim_id: int, identity: str):
    """Set the identity for a claim."""
    conn = get_conn()
    conn.execute("UPDATE claims SET identity = ? WHERE id = ?", (identity, claim_id))
    conn.commit()


def update_claim_basis(claim_id: int, basis: str):
    """Update the basis for a claim."""
    conn = get_conn()
    conn.execute("UPDATE claims SET basis = ? WHERE id = ?", (basis, claim_id))
    conn.commit()


def insert_dropped_claim(case_id: int, run_id: int, text: str, cites: list[str],
                         reason: str, conn=None) -> int:
    """Insert a record of a dropped claim."""
    if conn is None:
        conn = get_conn()
    cites_json = json.dumps(cites)
    cur = conn.execute(
        "INSERT INTO dropped_claims (case_id, run_id, text, cites, reason) VALUES (?, ?, ?, ?, ?)",
        (case_id, run_id, text, cites_json, reason)
    )
    conn.commit()
    return cur.lastrowid

