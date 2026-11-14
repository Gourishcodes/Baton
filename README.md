# Baton

Baton is a fail-closed engineering handover system that extracts structured, verifiable claims from scattered project sources such as chats, task boards, and notes. It partitions every source line and extracted claim by clearance labels (Team, Finance, HR) so sensitive records never leak to unauthorized roles or language model contexts. Deterministic rule engines verify every citation, detect contradictions, prompt the departing engineer to fill knowledge gaps, and assemble a clean handover dossier that closes upon successor acknowledgement.

## Quickstart

Run locally in four commands:

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .\.venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn app.main:app --port 8000
```

Open [http://localhost:8000](http://localhost:8000) and click **Open the demo case**.

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

## Limits

- **Role switching**: The role selector switches personas via signed cookies for evaluation and demo purposes; it is not an enterprise authentication or identity provider system.
- **Canary scanning**: Scanning response bodies for planted canary strings is a strong fail-closed check, not a mathematical proof of confidentiality.
- **Metadata leakage**: Showing counts of hidden items reveals to lower clearances that confidential items exist.
- **Sample size**: The evaluation suite uses a small synthetic sample written for this challenge, not an industry-wide benchmark.
