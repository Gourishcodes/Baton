"""Line numbering for Baton sources.

Must produce byte-identical line_id, text, and hash to tools/number_lines.py.
"""
import csv
import hashlib
import io
import os

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")


def _h(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def number_text(text: str, kind: str, source_id: str) -> list[dict]:
    """Parse raw text into numbered lines, identical to tools/number_lines.py."""
    out = []
    if kind == "csv":
        for n, row in enumerate(csv.DictReader(io.StringIO(text)), 1):
            t = " | ".join(f"{k}={v}" for k, v in row.items() if v not in ("", None))
            out.append({"line_id": f"{source_id}:{n:03d}", "n": n, "text": t, "hash": _h(t)})
    else:
        n = 0
        for raw in text.splitlines():
            if raw.strip() == "":
                continue
            n += 1
            t = raw.strip()
            out.append({"line_id": f"{source_id}:{n:03d}", "n": n, "text": t, "hash": _h(t)})
    return out


def number_file(path: str, kind: str, source_id: str) -> list[dict]:
    """Read a file and return numbered lines."""
    with open(path, encoding="utf-8") as f:
        return number_text(f.read(), kind, source_id)


def load_all(data_dir: str = DATA) -> dict[str, list[dict]]:
    """Load all demo sources, keyed by source ID."""
    import json
    roles_path = os.path.join(data_dir, "roles.json")
    with open(roles_path, encoding="utf-8") as f:
        roles_data = json.load(f)

    result = {}
    for s in roles_data["sources"]:
        result[s["id"]] = number_file(
            os.path.join(data_dir, s["file"]), s["kind"], s["id"]
        )
    return result
