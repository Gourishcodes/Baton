"""Provider protocol for LLM layer in Baton.

Follows docs/SPEC.md section 11.
"""
from typing import Protocol, runtime_checkable


@runtime_checkable
class Provider(Protocol):
    def generate_json(self, system: str, user: str, schema: dict) -> dict:
        """Generate structured JSON adhering to the given schema.

        Args:
            system: System prompt instructions.
            user: User input text containing numbered source lines.
            schema: JSON schema dict constraining the output structure.

        Returns:
            Parsed JSON dict conforming to the schema.

        Raises:
            Exception on failure or timeout.
        """
        ...
