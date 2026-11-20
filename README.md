# Baton

Baton is a fail-closed engineering handover system that extracts structured, verifiable claims from scattered project sources such as chats, task boards, and notes. It partitions every source line and extracted claim by clearance labels (Team, Finance, HR) so sensitive records never leak to unauthorized roles or language model contexts. Deterministic rule engines verify every citation, detect contradictions, prompt the departing engineer to fill knowledge gaps, and assemble a clean handover dossier that closes upon successor acknowledgement.

## Quickstart

Run locally in four commands (note: `SECRET_KEY` is required in the environment for local runs):

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .\.venv\Scripts\activate
pip install -r requirements.txt
export SECRET_KEY="your-secret-key"  # On Windows: $env:SECRET_KEY="your-secret-key"
python -m uvicorn app.main:app --port 8000
```

Open [http://localhost:8000](http://localhost:8000) and click **Open the demo case**.

## About the hosted demo

The hosted demo replays one recorded extraction (gemini-3.5-flash-lite) of the synthetic demo sources. It never calls a model and has no API key for the demo case, ensuring deterministic evaluation. Everything after extraction runs live on every click: citation checks, access control, conflict detection, sticky approvals and the leak test. In Round 3, live extraction on user-uploaded cases is fully implemented and operational via the Gemini API with structured output schemas. The demo is on a free host, so the first load after idle can take about a minute. Everyone shares one demo case; if it shows Closed, click Reset demo.

## Running Tests

All tests run locally without network access or live API keys:

```bash
# 1. Run full test suite (parsers, verification, access, flows, leak detection, core loop)
pytest -q

# 2. Run the automated leak test (scans every view and role for planted canary strings)
python -m app.leaktest

# 3. Run the accuracy and citation benchmark against the key facts
python -m app.eval

# 4. Run the design rules audit (requires server running on port 8000)
BATON_URL=http://localhost:8000 pytest tests/test_design_rules.py -q
```

## Architecture

Baton runs as a single-process FastAPI service backed by local SQLite storage and server-rendered Jinja2/HTMX templates styled with a hand-written 10-color token stylesheet. Source documents are partitioned by access label so that extraction jobs never combine data from disjoint clearance levels, running against structured LLM providers with schemas or reading from a committed offline demo cache. Extracted claims pass through deterministic Python verification functions (`verify.py`) to validate line citations, compute supersessions, detect contradictions, and flag unanswered gaps. Central access control (`access.visible`) acts as the single authority on every database read, while an ASGI wire-check middleware scans full response bodies and fails closed if any canary string unauthorized for the current persona appears.

## The Data

All project sources and test fixtures are synthetic data written by the team with Claude. The dataset simulates a handover for a food-delivery notification service, including team chats, vendor emails, contractor agreements, and task-board rows with intentionally planted canary strings and deliberate conflict traps.

## AI Disclosure

Antigravity and Claude wrote most of the code across the application, verification engine, access rules, tests, and documentation, as detailed in [`AI_DISCLOSURE.md`](AI_DISCLOSURE.md). When configured for live extraction, a free-tier model runs inside the product and its provider may log inputs, so use only synthetic data.

## Limits & Operational Disclosures

1. **Multi-tenant vs multi-user**: Baton is a multi-user application within a single database using case scoping (`case_id`) and label-based access clearance (`holds`), not a hard multi-tenant system with separate database instances or tenant-level encrypted storage.
2. **Provider logging**: When configured for live extraction, requests are sent to external LLM providers (e.g. Gemini free tier), which may retain and log prompts and generated outputs for model improvement. Users must never upload confidential, proprietary, or personal data to live cases.
3. **Network boundary**: External model calls are strictly isolated to `app/extract.py`. All other operations—including parsers, rule verification, access control evaluation, Markdown/Print export generation, and leak tests—run 100% offline with zero outbound network calls.
4. **Single-file SQLite & ephemeral disk reset**: Application state and rate counters (`provider_calls`, `run_events`, `login_attempts`) are stored in a single SQLite database file (`baton.db`). On free hosting tiers with ephemeral containers/disks, container sleep/restarts reset the database and runtime cache, clearing non-demo user accounts, uploaded cases, and daily call counters.
5. **Token-based invites**: Case sharing uses single-use, 7-day cryptographic invite tokens. Links must be shared securely over private communication channels. Redeeming an invite never degrades or strips an existing member's role or clearance, and a case always preserves at least one owner.
6. **Planted-string scan covers demo case only**: The automated 9-canary scan and WireCheckMiddleware cover the seeded demo case (Case 1) only. On user-created cases, confidential information is protected exclusively by the access control filter (`access.visible`), which deterministically enforces label clearances.
7. **Sign-up without email verification or password reset**: User account registration is open (bounded by `MAX_USERS`, default 100) without email confirmation or password-reset flows. Forgotten passwords require creating a new account.
8. **Live accuracy for new cases is not measured**: Accuracy metrics (recall, precision, TR01-TR07 traps) are computed exclusively against the ground-truth answer key of the seeded demo case. No accuracy figure exists for newly uploaded cases; all extracted claims start Pending and are citation-checked by code.
9. **"What to do first" can list overdue items and duplicate actions**: The prioritization view aggregates action items across all uploaded sources. Duplicate mentions across different files or items with past target dates may appear.
10. **Demo clock**: The seeded demo case operates on a frozen reference clock (14 days before Meera Joshi's departure) for reproducible evaluation, whereas user-created cases use live UTC timestamps.
11. **Role switching**: The role selector switch is exclusively available for the four seeded demo accounts. Real accounts see their identity, clearance tags, and Sign out.
12. **Metadata leakage**: Showing counts of hidden items reveals to lower clearances that confidential items exist.
13. **Sample size**: The evaluation suite uses a small synthetic sample written for this challenge, not an industry-wide benchmark.

## Environment Variables

Copy `.env.example` to `.env` to configure the application:

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | required | Secret key for signed session cookies and CSRF Origin validation |
| `DEMO_PASSWORD` | `baton-demo-2026` | Password for seeded demo accounts |
| `GEMINI_API_KEY` | `""` | Gemini API key for live LLM extraction |
| `LIVE_ENABLED` | `1` | Kill-switch for live extraction (`1` = enabled, `0` = disabled) |
| `LIVE_DAILY_CALL_CAP` | `50` | Maximum provider calls across all users per day |
| `MAX_USERS` | `100` | Maximum registered non-demo user accounts |
| `RUNS_PER_USER_PER_DAY` | `3` | Maximum live extraction runs per user account daily |
| `RUNTIME_CACHE_DIR` | `./runtime_cache` | Directory caching live extraction results |
| `BATON_DB` | `baton.db` | SQLite database file path |
| `TRUSTED_PROXY_HOPS` | `1` | Reverse proxy hops for client IP extraction |
