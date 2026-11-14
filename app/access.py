"""Access control for Baton.

visible(item_labels, persona) is the ONLY authority for visibility decisions.
Templates never filter. Routes never filter. Repository functions call this.
"""
import json
import os

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")


def _load_roles(data_dir: str = DATA) -> dict:
    with open(os.path.join(data_dir, "roles.json"), encoding="utf-8") as f:
        return json.load(f)["roles"]


# Cache roles on first load
_roles_cache: dict | None = None


def get_roles(data_dir: str = DATA) -> dict:
    """Return the roles dict from roles.json."""
    global _roles_cache
    if _roles_cache is None:
        _roles_cache = _load_roles(data_dir)
    return _roles_cache


def reset_cache():
    """Clear cached roles (for testing)."""
    global _roles_cache
    _roles_cache = None


def persona_holds(persona: str, data_dir: str = DATA) -> set[str]:
    """Return the set of label groups a persona holds."""
    roles = get_roles(data_dir)
    if persona not in roles:
        return set()
    return set(roles[persona]["holds"])


def visible(item_labels: list[str] | set[str], persona: str, data_dir: str = DATA) -> bool:
    """An item is visible if its label set is a subset of what the persona holds.

    This is the ONLY place that decides visibility. Nothing else filters.
    """
    if not item_labels:
        # Items with no labels are visible to everyone
        return True
    holds = persona_holds(persona, data_dir)
    return set(item_labels).issubset(holds)


def persona_display(persona: str, data_dir: str = DATA) -> str:
    """Return the display name for a persona."""
    roles = get_roles(data_dir)
    if persona not in roles:
        return persona
    return roles[persona]["display"]
