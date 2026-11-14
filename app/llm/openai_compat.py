"""OpenAI-compatible fallback provider for Baton.

Follows docs/SPEC.md sections 11.
"""
import json
import os
from typing import Any

from .base import Provider


class OpenAICompatProvider(Provider):
    """Fallback LLM provider using OpenAI-compatible HTTP endpoint."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None):
        self.base_url = base_url or os.environ.get("FALLBACK_BASE_URL")
        self.api_key = api_key or os.environ.get("FALLBACK_API_KEY")
        self.model = model or os.environ.get("FALLBACK_MODEL")

    def generate_json(self, system: str, user: str, schema: dict) -> dict:
        """Call the OpenAI-compatible endpoint with JSON format and 45s timeout."""
        try:
            import openai
        except ImportError as e:
            raise RuntimeError("openai package is required for OpenAICompatProvider") from e

        if not self.api_key:
            raise ValueError("FALLBACK_API_KEY is not configured")
        if not self.model:
            raise ValueError("FALLBACK_MODEL is not configured")

        client_kwargs: dict[str, Any] = {
            "api_key": self.api_key,
            "timeout": 45.0,
        }
        if self.base_url:
            client_kwargs["base_url"] = self.base_url

        client = openai.OpenAI(**client_kwargs)

        # Append schema requirements to system instructions
        system_with_schema = (
            f"{system}\n\n"
            f"You MUST return valid JSON matching this JSON Schema:\n"
            f"{json.dumps(schema, indent=2)}"
        )

        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_with_schema},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )

        content = response.choices[0].message.content
        if not content:
            raise ValueError("Empty response from OpenAI-compatible provider")

        return json.loads(content)
