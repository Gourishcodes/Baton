"""Build test fixtures from data/answer_key.json.

Generates tests/fixtures/model_output_{team,finance,hr}.json, each containing
claims and gaps as a model would return them for that label set's call.

For Conflict facts, the two side-claims are placed in their respective label-set
fixture based on which line they cite (each cite belongs to exactly one source
label set).

D02 and D03 are set up so D03 carries "supersedes" for D02.

Three deliberate defects in the team fixture:
  1. One claim citing a line ID that does not exist (FAKE:999)
  2. One claim in the team file citing a finance line (F1:001)
  3. One claim with no cites
"""
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(SCRIPT_DIR, "..")
DATA = os.path.join(ROOT, "data")


def load_answer_key():
    with open(os.path.join(DATA, "answer_key.json"), encoding="utf-8") as f:
        return json.load(f)


def _source_label_for_line(line_id: str, sources: list) -> str:
    """Given a line_id like 'C1:021', determine which source label set it belongs to."""
    source_id = line_id.split(":")[0]
    for src in sources:
        if src["id"] == source_id:
            return tuple(sorted(src["labels"]))
    return ("team",)  # fallback


def build_fixtures():
    key = load_answer_key()
    facts = key["facts"]
    sources = key["sources"]

    # We have three calls: team, finance, hr (each is a single-label set)
    fixtures = {
        "team": {"claims": [], "gaps": []},
        "finance": {"claims": [], "gaps": []},
        "hr": {"claims": [], "gaps": []},
    }

    def label_key(labels_tuple):
        """Map a labels tuple to the fixture file key."""
        return "_".join(labels_tuple)

    for fact in facts:
        if fact["type"] == "Gap":
            # Gaps go to the label set of their first cite
            if fact["line_ids"]:
                ls = _source_label_for_line(fact["line_ids"][0], sources)
            else:
                ls = tuple(sorted(fact["labels"]))
            lk = label_key(ls)
            if lk not in fixtures:
                fixtures[lk] = {"claims": [], "gaps": []}
            fixtures[lk]["gaps"].append({
                "question": fact["text"],
                "why": f"From answer key fact {fact['id']}",
                "cites": fact["line_ids"],
            })

        elif fact["type"] == "Conflict":
            # For conflicts, each side-claim goes to the label set of its cited line
            for i, (line_id, value) in enumerate(zip(fact["line_ids"], fact["values"])):
                ls = _source_label_for_line(line_id, sources)
                lk = label_key(ls)
                if lk not in fixtures:
                    fixtures[lk] = {"claims": [], "gaps": []}
                claim = {
                    "type": "Decision",
                    "text": f"{fact['subject']} {fact['attribute']}: {value}",
                    "subject": fact.get("subject", ""),
                    "attribute": fact.get("attribute", "none"),
                    "value": value,
                    "owner": None,
                    "due": None,
                    "event_date": None,
                    "recurring": False,
                    "not_on_task_board": False,
                    "supersedes": None,
                    "cites": [line_id],
                }
                fixtures[lk]["claims"].append(claim)
        else:
            # Regular claims go to the label set of the source
            if fact["line_ids"]:
                ls = _source_label_for_line(fact["line_ids"][0], sources)
            else:
                ls = tuple(sorted(fact["labels"]))
            lk = label_key(ls)
            if lk not in fixtures:
                fixtures[lk] = {"claims": [], "gaps": []}

            claim = {
                "type": fact["type"],
                "text": fact["text"],
                "subject": fact.get("subject", fact["text"][:40]),
                "attribute": fact.get("attribute", "none"),
                "value": fact.get("value"),
                "owner": fact.get("owner"),
                "due": fact.get("due"),
                "event_date": fact.get("date"),
                "recurring": fact.get("recurring", False),
                "not_on_task_board": fact.get("untracked_promise", False),
                "cites": fact["line_ids"],
            }

            # Handle supersession: D03 supersedes D02
            if fact["id"] == "D03":
                claim["supersedes"] = {
                    "subject": "sms vendor",
                    "attribute": "vendor",
                    "old_value": "textly",
                }
                claim["subject"] = "sms vendor"
                claim["attribute"] = "vendor"
                claim["value"] = "pingwire"

            if fact["id"] == "D02":
                claim["subject"] = "sms vendor"
                claim["attribute"] = "vendor"
                claim["value"] = "textly"

            fixtures[lk]["claims"].append(claim)

    # Add three deliberate defects to the team fixture
    team = fixtures["team"]

    # Defect 1: claim citing a line ID that does not exist
    team["claims"].append({
        "type": "Decision",
        "text": "DEFECT: This claim cites a non-existent line.",
        "subject": "fake reference",
        "attribute": "none",
        "value": None,
        "owner": None,
        "due": None,
        "event_date": None,
        "recurring": False,
        "not_on_task_board": False,
        "supersedes": None,
        "cites": ["FAKE:999"],
    })

    # Defect 2: team claim citing a finance line
    team["claims"].append({
        "type": "Decision",
        "text": "DEFECT: This team claim cites a finance line.",
        "subject": "cross label cite",
        "attribute": "none",
        "value": None,
        "owner": None,
        "due": None,
        "event_date": None,
        "recurring": False,
        "not_on_task_board": False,
        "supersedes": None,
        "cites": ["F1:001"],
    })

    # Defect 3: claim with no cites
    team["claims"].append({
        "type": "Decision",
        "text": "DEFECT: This claim has no cites at all.",
        "subject": "no citations",
        "attribute": "none",
        "value": None,
        "owner": None,
        "due": None,
        "event_date": None,
        "recurring": False,
        "not_on_task_board": False,
        "supersedes": None,
        "cites": [],
    })

    # Write fixtures
    fixtures_dir = os.path.join(SCRIPT_DIR, "fixtures")
    os.makedirs(fixtures_dir, exist_ok=True)

    for label_name, data in fixtures.items():
        path = os.path.join(fixtures_dir, f"model_output_{label_name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Wrote {path} ({len(data['claims'])} claims, {len(data['gaps'])} gaps)")


if __name__ == "__main__":
    build_fixtures()
