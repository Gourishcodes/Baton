"""Test 5: access.visible truth table for all personas and label sets."""
import os
import sys
import pytest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

from app import access
from app.auth import Principal, demo_principal


def _get_test_principal(name: str) -> Principal:
    if name == "nobody":
        return Principal(99, "Nobody", "nobody@example.com", 1, "viewer", False, frozenset())
    return demo_principal(name)


class TestAccessVisible:
    """SPEC 14.5: access.visible truth table for all personas and label sets."""

    # Persona holds:
    # leaver:      {team}
    # successor:   {team}
    # lead:        {team, finance, hr}
    # finance_ops: {team, finance}

    @pytest.mark.parametrize("labels,persona,expected", [
        # Team labels - visible to everyone
        (["team"], "leaver", True),
        (["team"], "successor", True),
        (["team"], "lead", True),
        (["team"], "finance_ops", True),

        # Finance labels - visible only to lead and finance_ops
        (["finance"], "leaver", False),
        (["finance"], "successor", False),
        (["finance"], "lead", True),
        (["finance"], "finance_ops", True),

        # HR labels - visible only to lead
        (["hr"], "leaver", False),
        (["hr"], "successor", False),
        (["hr"], "lead", True),
        (["hr"], "finance_ops", False),

        # Cross-label: team + finance - visible only to lead and finance_ops
        (["team", "finance"], "leaver", False),
        (["team", "finance"], "successor", False),
        (["team", "finance"], "lead", True),
        (["team", "finance"], "finance_ops", True),

        # Cross-label: team + hr - visible only to lead
        (["team", "hr"], "leaver", False),
        (["team", "hr"], "successor", False),
        (["team", "hr"], "lead", True),
        (["team", "hr"], "finance_ops", False),

        # Cross-label: finance + hr - visible only to lead
        (["finance", "hr"], "leaver", False),
        (["finance", "hr"], "successor", False),
        (["finance", "hr"], "lead", True),
        (["finance", "hr"], "finance_ops", False),

        # All three: team + finance + hr - visible only to lead
        (["team", "finance", "hr"], "leaver", False),
        (["team", "finance", "hr"], "successor", False),
        (["team", "finance", "hr"], "lead", True),
        (["team", "finance", "hr"], "finance_ops", False),

        # Empty labels - not visible to anyone
        ([], "leaver", False),
        ([], "successor", False),
        ([], "lead", False),
        ([], "finance_ops", False),
    ])
    def test_visible(self, labels, persona, expected):
        p = _get_test_principal(persona)
        assert access.visible(labels, p) == expected, \
            f"visible({labels}, {persona}) should be {expected}"

    def test_empty_labels_not_visible(self):
        """visible() must return False for an item whose label list is empty."""
        for name in ["leaver", "successor", "lead", "finance_ops", "nobody"]:
            p = _get_test_principal(name)
            assert access.visible([], p) is False
            assert access.visible(set(), p) is False

    def test_unknown_persona_sees_nothing(self):
        """An unknown persona holds nothing."""
        p = _get_test_principal("nobody")
        assert access.visible(["team"], p) is False
        assert access.visible([], p) is False
