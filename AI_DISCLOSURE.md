# AI Disclosure

Every module below was written or substantially shaped by AI (Antigravity / Claude / Gemini). Listed as: path, what it does.

- `app/__init__.py` — Package init.
- `app/parsers.py` — Line numbering for source files, byte-identical to tools/number_lines.py.
- `app/db.py` — SQLite schema, connection management, demo case loader, CRUD helpers.
- `app/access.py` — Central access control: `visible(item_labels, persona)` is the only authority.
- `app/repo.py` — Persona-filtered repository queries; every read goes through access.visible.
- `app/verify.py` — Citation validation, basis assignment, supersession, conflict detection, deterministic gaps.
- `app/review.py` — Claim identity hashing, sticky decision matching (exact + Jaccard), version snapshots, demo seeding.
- `app/kernel_demo.py` — Trust kernel verification runner (no-model demo check).
- `app/llm/base.py` — Provider protocol definition for structured JSON generation.
- `app/llm/gemini.py` — Primary LLM provider using google-genai with JSON schema output and 45s timeout.
- `app/llm/openai_compat.py` — Fallback LLM provider for any OpenAI-compatible API with 45s timeout.
- `app/extract.py` — Line partitioning by label set, Pydantic validation, retry/fallback, demo_cache management.
- `app/eval.py` — Accuracy and benchmark evaluation against data/answer_key.json.
- `scripts/build_cache.py` — Tool to run live extraction per label set and generate demo_cache/*.json.
- `tests/make_fixtures.py` — Generates model output fixtures from data/answer_key.json with three deliberate defects.
- `tests/test_parsers.py` — Tests 1 & 2: numbering equivalence and anchor resolution.
- `tests/test_verify.py` — Tests 3 & 4: fabricated cite, cross-label cite, supersession, conflict detection, deterministic gaps.
- `tests/test_access.py` — Test 5: access.visible truth table for all personas and label sets.
- `tests/test_review.py` — Tests 6 & 7: sticky decisions, Jaccard matching, cross-label conflict visibility.
- `tests/test_model_layer.py` — Tests for schema validation retry, provider fallback, zero-network cache_only, and eval execution.
- `requirements.txt` — Pinned dependency versions.
