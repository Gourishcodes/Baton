"""Gemini provider using google-genai SDK for Baton.

Follows docs/SPEC.md section 11.
"""
import json
import os
from typing import Any

from .base import Provider


class GeminiProvider(Provider):
    """Primary LLM provider using the google-genai SDK."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self.model = model or os.environ.get("LLM_MODEL")

    def generate_json(self, system: str, user: str, schema: dict) -> dict:
        """Call Gemini via google-genai with JSON schema constraint and 45s timeout."""
        try:
            from google import genai
            from google.genai import types
        except ImportError as e:
            raise RuntimeError("google-genai package is required for GeminiProvider") from e

        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is not configured")
        if not self.model:
            raise ValueError("LLM_MODEL is not configured")

        client = genai.Client(
            api_key=self.api_key,
            http_options=types.HttpOptions(timeout=45_000),
        )

        config_kwargs: dict[str, Any] = {
            "system_instruction": system,
            "response_mime_type": "application/json",
            "temperature": 0.0,
        }
        if schema:
            config_kwargs["response_schema"] = schema

        config = types.GenerateContentConfig(**config_kwargs)

        response = client.models.generate_content(
            model=self.model,
            contents=user,
            config=config,
        )

        text = response.text
        if not text:
            raise ValueError("Empty response from Gemini provider")

        return json.loads(text)
