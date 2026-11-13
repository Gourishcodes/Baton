"""Reference line numbering for Baton sources.

The app's parsers MUST produce byte-identical line IDs and line text, because
data/answer_key.json and the eval script refer to these IDs.

Rules
-----
chat (.txt)  one message per non-empty line; text is the line stripped.
md   (.md)   every non-blank line (headings included); text is the line stripped.
csv  (.csv)  header row excluded; every data row is one line; text is
             "k=v | k=v | ..." over the columns, skipping empty values.
ID           f"{source_id}:{n:03d}", n counts the numbered lines of that source from 1.
hash         sha1 of the line text, first 12 hex chars (used for staleness in Round 3).
"""
import csv
import hashlib
import io
import os

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")

SOURCES = [
    {"id": "C1", "file": "chat_team.txt", "kind": "chat", "labels": ["team"]},
    {"id": "T1", "file": "tasks.csv", "kind": "csv", "labels": ["team"]},
    {"id": "N1", "file": "notes_team.md", "kind": "md", "labels": ["team"]},
    {"id": "F1", "file": "chat_finance.txt", "kind": "chat", "labels": ["finance"]},
    {"id": "H1", "file": "notes_hr.md", "kind": "md", "labels": ["hr"]},
]


def _h(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def number_text(text, kind, source_id):
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


def number_file(path, kind, source_id):
    with open(path, encoding="utf-8") as f:
        return number_text(f.read(), kind, source_id)


def load_all(data_dir=DATA):
    result = {}
    for s in SOURCES:
        result[s["id"]] = number_file(os.path.join(data_dir, s["file"]), s["kind"], s["id"])
    return result


if __name__ == "__main__":
    for sid, lines in load_all().items():
        print(sid, len(lines), "lines")
        for l in lines[:2]:
            print("  ", l["line_id"], l["text"][:90])
