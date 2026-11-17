"""Authentication and Principal definitions for Baton.

Defines the Principal dataclass and demo_principal helper.
"""
from dataclasses import dataclass
import json
import os
from . import db

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")


@dataclass(frozen=True)
class Principal:
    user_id: int
    name: str
    email: str
    case_id: int
    role: str  # approver, leaver, successor, viewer
    is_owner: bool
    holds: frozenset[str]

    def __post_init__(self):
        if not isinstance(self.holds, frozenset):
            object.__setattr__(self, "holds", frozenset(self.holds))


def _load_roles_json(data_dir: str = DATA) -> dict:
    with open(os.path.join(data_dir, "roles.json"), encoding="utf-8") as f:
        return json.load(f)["roles"]


def demo_principal(name: str, case_id: int = 1) -> Principal:
    """Return the Principal for the four demo roles.

    lead / approver = Divya Menon, owner and approver, holds team/finance/hr
    leaver = Meera Joshi, team
    successor = Arjun Rao, team
    finance_ops / viewer = Sameer Kulkarni, viewer, team+finance

    Reads names and clearance from data/roles.json and the case row.
    """
    roles_data = _load_roles_json()

    # Look up case row if available
    case_row = None
    try:
        conn = db.get_conn()
        row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if row:
            case_row = dict(row)
    except Exception:
        pass

    normalized = name.lower().strip()
    if normalized in ("lead", "approver", "divya", "divya menon"):
        p_name = (case_row and case_row.get("approver")) or "Divya Menon"
        return Principal(
            user_id=1,
            name=p_name,
            email="divya@example.com",
            case_id=case_id,
            role="approver",
            is_owner=True,
            holds=frozenset(roles_data["lead"]["holds"]),
        )
    elif normalized in ("leaver", "meera", "meera joshi"):
        p_name = (case_row and case_row.get("leaver")) or "Meera Joshi"
        return Principal(
            user_id=2,
            name=p_name,
            email="meera@example.com",
            case_id=case_id,
            role="leaver",
            is_owner=False,
            holds=frozenset(roles_data["leaver"]["holds"]),
        )
    elif normalized in ("successor", "arjun", "arjun rao"):
        p_name = (case_row and case_row.get("successor")) or "Arjun Rao"
        return Principal(
            user_id=3,
            name=p_name,
            email="arjun@example.com",
            case_id=case_id,
            role="successor",
            is_owner=False,
            holds=frozenset(roles_data["successor"]["holds"]),
        )
    elif normalized in ("finance_ops", "finance", "viewer", "sameer", "sameer kulkarni"):
        return Principal(
            user_id=4,
            name="Sameer Kulkarni",
            email="sameer@example.com",
            case_id=case_id,
            role="viewer",
            is_owner=False,
            holds=frozenset(roles_data["finance_ops"]["holds"]),
        )
    else:
        raise ValueError(f"Unknown demo role/persona: {name}")
