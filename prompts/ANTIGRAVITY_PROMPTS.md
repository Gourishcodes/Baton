# Antigravity prompts for Baton (Round 2)

Paste one prompt per task, in order. Do not paste two at once. After each one, run its gate command yourself before you paste the next. If the gate fails, paste the failure output back and say "fix this, change nothing else".

Why five prompts and not one: a single giant prompt makes the agent skim. These are short, each ends with a command that proves it worked, and none of them restates the spec. `docs/SPEC.md` is the behaviour, `docs/DESIGN.md` plus `static/baton.css` plus `docs/design-reference.html` is the look.

Secrets: put `GEMINI_API_KEY` in `.env` (gitignored). Never paste a key into a prompt or commit it.

---

## Prompt 0: context (paste first, in a fresh task)

````text
You are building Baton, a handover tool, inside this repo. The work is judged on a deployed link that a stranger can open and use, so the working core loop matters more than anything else.

Read these in this order, fully, before you write any code:
1. docs/SPEC.md   (behaviour; if anything I say conflicts with it, SPEC wins, except the corrections below)
2. docs/DESIGN.md (look and UI rules)
3. static/baton.css and docs/design-reference.html (the only visual source of truth)
4. data/answer_key.json, data/roles.json, data/canaries.json, tools/number_lines.py

Corrections to SPEC:
- The Round 2 deadline is Sun 15 Nov 2026 10:00 IST, not Sat 14 Nov. Ignore the date on SPEC line 10.
- Ignore the folder design_reference/ completely. It is obsolete. Do not read, copy or import from it.

Protected, never edit: data/, tools/number_lines.py, tools/build_answer_key.py, docs/SPEC.md, docs/DESIGN.md, tests/test_design_rules.py, and everything that is already in static/baton.css (you may only append new rules at the very bottom, under a new comment line "/* additions */"). If you think one of these is wrong, stop and tell me.

Working rules:
- Stack is exactly what SPEC section 1 says. No Tailwind, no CSS framework, no JS framework, no CDN at runtime.
- Work in small steps. After each step run the tests and show me the real output. Never say something passes without running it.
- Never write a result number (recall, precision, leak count) anywhere in code or templates. Numbers come from running the code.
- Keep a file AI_DISCLOSURE.md. For every module you write, add one line: path, what it does. I will use it in the README.
- Keep a file KNOWN_ISSUES.md. Append anything that does not work yet, as you find it, in plain words.

Do not build anything yet. Reply with: (a) a one-paragraph summary of Baton in your own words, (b) the repo layout you propose (app/, templates/, static/, tests/, demo_cache/, scripts/), (c) any contradiction you found between SPEC.md, DESIGN.md and the data files. Then stop and wait.
````

Gate: its reply lists a layout and either "no contradictions" or real ones. Answer any it finds before continuing.

---

## Prompt 1: trust kernel, no model, no UI (about 3 hours)

````text
Build the trust kernel. No model calls and no HTML in this task.

Implement, following docs/SPEC.md:
- section 3: app/parsers.py. Must return line_id, text and hash identical to tools/number_lines.py for every file in data/. A test compares them.
- section 4: app/db.py with the schema, SQLite via stdlib sqlite3. Load data/ into a case using data/roles.json for sources and labels.
- section 6: app/verify.py. Cite validation (line exists AND belongs to the call's label set), basis assignment (sourced or inferred), supersession, conflict detection across ALL label sets (token overlap coefficient >= 0.8 conflict, 0.5 to 0.8 possible), deterministic gaps from empty-assignee task rows.
- section 7: app/access.py. visible(item_labels, persona) is the ONLY place that decides visibility. Every repository function in app/repo.py takes a persona and filters through it. Nothing else filters.
- section 8: app/review.py with claim identity, the sticky decision matching (exact, Jaccard >= 0.6 inherit, 0.34 to 0.6 rejected becomes possibly_previously_rejected) and edited-claim locking.

Test it without a model. Write tests/make_fixtures.py that builds tests/fixtures/model_output_{team,finance,hr}.json from data/answer_key.json: for each fact in that label set, one claim whose cites are the fact's line_ids, with subject, attribute and value filled so that X01 and X02 form conflicts and D03 carries "supersedes" D02. Then add three deliberate defects: one claim citing a line ID that does not exist, one claim in the team file citing a finance line, one claim with no cites.

Write the tests listed in SPEC section 14, numbers 1 to 7 (not 8 or 9 yet). Add app/kernel_demo.py, run as: python -m app.kernel_demo, which loads the fixtures and prints a table: claims per basis, conflicts found, which persona sees which key fact (use the visible_to field in the answer key as the expectation and print any mismatch).

Do not touch the UI, the LLM layer or deployment.
````

Gate: `pytest -q` green, and `python -m app.kernel_demo` prints zero visibility mismatches, the fake cite marked invalid, the cross-label cite rejected, and X02 invisible to successor.

---

## Prompt 2: model layer, extraction, cache, accuracy check (about 3 hours)

````text
Build the model layer and the accuracy check, following docs/SPEC.md sections 5, 11 and 12. No UI.

- app/llm/base.py: the Provider protocol. app/llm/gemini.py with google-genai and JSON output constrained to the schema. app/llm/openai_compat.py as the single fallback. Model names and keys only from environment variables (SPEC section 11). Do not hard-code any model name.
- app/extract.py: group lines by label set, one call per label set, using the system prompt from SPEC section 5.4 verbatim. Validate output with pydantic, retry once, then fall back to the other provider, then fail the run with a visible message and keep the previous good run. 45 second timeout.
- Cache exactly as SPEC section 11 says: sha256 key, files in demo_cache/. LLM_MODE has three values; cache_only must never open a network connection (add a test that fails if it does).
- scripts/build_cache.py: runs live once per label set and writes demo_cache/. I will run this myself with my key.
- app/eval.py, run as: python -m app.eval. Implements SPEC section 12 exactly: recall overall, by type and by label set; precision against the key; citation validity; traps TR01 to TR07 each as pass or fail with the evidence line; the string check for the injected instruction; the must_not_assert list printed for a hand check; the honesty line. Print unmatched claims for hand review.

Tests: the schema validator rejects bad output and retries once; a provider failure falls back; cache_only makes zero network calls; eval runs from fixtures with no model and prints a table.

Do not run anything that needs my API key. When done, tell me the exact command to build the cache.
````

Gate: `LLM_MODE=cache_only python -m app.eval` runs on fixtures. Then you run `python scripts/build_cache.py` with your key, commit `demo_cache/`, and run `LLM_MODE=cache_only python -m app.eval` again. This is the Thursday gate from the plan, now late: the real numbers decide what you can claim in the PDF.

---

## Prompt 3: UI shell and the claims screen (about 4 hours)

````text
Build the interface for the Claims screen and the shell around it. Read docs/DESIGN.md again first. Everything visual comes from static/baton.css and docs/design-reference.html. You do not design anything; you assemble.

Build, in this order:
1. templates/base.html: skip link, .bar (wordmark SVG exactly as in the reference page, case name, persona radiogroup, the "Role switch for the demo. Not authentication." line), .tabs with counts, a block for content, and the .wire strip at the foot.
2. templates/_macros.html: macros claim(), cite(), tag(), status(), redacted_claim(), srcline(), wire(). All repeated markup lives here once. Copy the markup from docs/design-reference.html character for character, changing only the data.
3. app/main.py routes from SPEC section 10 for: /, POST /role (signed cookie via itsdangerous; the persona switch is a normal form post and a full page load, not HTMX, so the redaction is redrawn by the server), POST /demo/open, /case/{id}/sources, /case/{id}/claims, /case/{id}/claims/{cid}/source, /healthz.
4. Home page exactly as docs/DESIGN.md section 5 describes: no hero, no illustration, one click into the demo case.
5. Claims screen: legend, filters (HTMX swap of the ledger only), the ledger, hidden items rendered through redacted_claim() with the exact fixed markup, the footer line "N items are outside your clearance. Ask the approver, {name}.", the source drawer (HTMX into #drawer, scrolls to the cited line, highlights it with srcline--cited), approve/edit/reject buttons only for the lead persona (they can post and swap the single article; the decision logic lands in the next task).
6. static/baton.js: keyboard J/K/A/E/R/Enter for the lead persona only, under 60 lines, no libraries.
7. The wire middleware from SPEC section 7: fail closed, append the strip, never print a canary.
8. A request for a hidden claim's drawer returns the same 404 body as a made-up id.

Strict visual rules (the test will check them): no gradients, no shadows, no radius above 2px, only the tokens in static/baton.css, only IBM Plex Sans, IBM Plex Mono and Newsreader, no emoji, no icons, no toasts, no modals, no spinners, no animation except the existing "draw". If a component you need does not exist, append it at the very bottom of static/baton.css under a new comment line "/* additions */" (change nothing already in the file) using tokens only and add it to docs/design-reference.html too, and tell me.

To start the demo: seed the case from data/ and demo_cache/ with LLM_MODE=cache_only. The demo must open on the Claims screen as the Approver with the drawer already open on C1:026.

Prove it:
- Run the app, then BATON_URL=http://localhost:8000 pytest tests/test_design_rules.py -q (it audits every route in its ROUTES list for leaver, lead and successor at 1440 and 390 wide; routes you have not built yet will return a 404 page, so list which routes returned 404 and judge style failures only on the routes that exist. Do not edit the test file).
- Take screenshots at 1440 wide of /case/1/claims as Approver and as Successor, and at 390 wide as Approver. Put docs/design-reference.html next to them and list every visible difference you see. Fix the differences. Show me the final screenshots.
````

Gate: the design test passes for the routes that exist, and the Successor screenshot shows black bars where Finance and HR items were, with the footer count and the wire strip. This is your 20-second demo moment. If it looks like the reference page, move on. If not, paste the screenshot and say what differs.

---

## Prompt 4: gaps, review, handover, trust page, leak test (about 4 hours)

````text
Finish the product loop. Follow docs/SPEC.md sections 7, 8, 9, 10, 13 and 14, and docs/DESIGN.md section 5, using only existing components.

Build:
- Gaps screen and the answer flow (leaver or lead answers; label selector limited to labels the answerer holds; creates an Attested claim that starts Pending). A request to answer a hidden gap returns the identical 404 body.
- Decisions: approve, edit, reject, restore, by the lead only, written to decisions keyed by identity (not claim id). Re-running extraction must keep rejected claims rejected and edited claims locked, and show "Possibly previously rejected. Check before approving." in the 0.34 to 0.6 range.
- Conflicts, Timeline, Handover (sections in the fixed order from SPEC section 8; only approved or edited claims; superseded under "History"), Approve handover (lead), Acknowledge receipt (successor, closes the case), Approvals.
- Seeded demo state and "Reset demo" exactly as SPEC section 8 says, with the banner that says what is seeded.
- /trust: the large mono counter, the measures table, "Run leak test" and "Run accuracy check" that stream rows. Always print the two honesty lines from docs/DESIGN.md.
- app/leaktest.py, run as: python -m app.leaktest. Every persona, every view listed in SPEC section 13, scanning full response bodies for every canary the persona must not see. Also assert every stub matches the fixed markup, and that the successor handover contains no label names.

Tests: SPEC section 14 numbers 6, 7, 8 and 9. For number 8, prove the leak test can fail: inject a canary into a template in a test, assert the leak test catches it, then remove it.

Then write tests/test_core_loop.py with Playwright, acting like a stranger who has never seen the product: open the home page, click Open the demo case, switch to Successor and assert the handover shows version 1 but not the pending claims, switch to Leaver and answer one gap, switch to Approver and approve that claim and the handover, switch to Successor, acknowledge, assert the case is Closed. No test may depend on a live model.

Prove it: run pytest -q, python -m app.leaktest, python -m app.eval and BATON_URL=http://localhost:8000 pytest tests/test_design_rules.py -q. Paste all four outputs. Take Approver and Successor screenshots of every route at 1440 and 390 wide and compare each to docs/design-reference.html; list and fix differences.
````

Gate: all four commands green, the leak test reports a count (not "safe"), and the core-loop test passes. If the schedule slips, SPEC section 16 and the Round 1 cut order apply: drop version history to a counter first, never the leak test, the filter, citation checking or sticky decisions.

Also assert that tab counts and filter-chip counts never differ between personas who hold the same labels.
Do not edit app/extract.py, do not change PROMPT_VERSION, and do not run scripts/build_cache.py. The committed demo_cache must stay as it is.

---

## Prompt 5: ship it (about 2 hours)

````text
Make Baton deployable and honest. Do not add features.

- Dockerfile (python slim, non-root, one container). The deployed default is LLM_MODE=cache_only so no model is ever called. SQLite lives on the container disk and is reseeded from data/ and demo_cache/ on every boot, so a restart never loses the demo. /healthz returns 200.
- A .env.example with every variable from SPEC section 11 and no values. Check that .gitignore excludes .env, *.db and __pycache__, and that no key appears anywhere in the repo history.
- README.md with: what Baton is in three sentences; how to run locally in four commands; how to run the tests; the architecture in one short diagram-free paragraph; the data (synthetic, written by the team with Claude); the AI disclosure (Antigravity and Claude wrote most of the code, listed from AI_DISCLOSURE.md; a free-tier model runs inside the product and its provider may log inputs, so use only synthetic data); the limits (role switch is not authentication, canary scan is not a proof, N hidden items count reveals that something exists, sample is small).
- Copy the real command outputs of python -m app.eval and python -m app.leaktest into docs/RESULTS.md with the date and commit hash. These are the only place numbers may be quoted from.
- KNOWN_ISSUES.md rewritten as a plain list a judge can read: what does not work yet, in one line each.
- A fresh-clone test: clone the repo into an empty directory, build the image, run it, open / in a headless browser with no cookies, and run tests/test_core_loop.py against it. Tell me exactly what failed, if anything.

List, as plain text I can paste into the submission form: (a) every finished and working feature, one per line; (b) technology and implementation details; (c) what changed from the Round 1 plan (use SPEC section 15, and the new deadline); (d) what does not work. Only include a feature if the fresh-clone test proves it.
````

Gate: the fresh-clone run passes. Then you deploy it, and open the live link, the repo and the PDF in a private window before you submit.

---

## Suggested schedule (now Fri ~16:30, deadline Sun 10:00)

| When | What |
|---|---|
| Fri evening | Prompts 0, 1, 2. Run your key once to build the cache. Gate: kernel tests green and real eval numbers exist |
| Sat morning | Prompt 3. Gate: the Successor screenshot looks like the reference |
| Sat afternoon | Prompt 4. Gate: four commands green plus the core-loop test |
| Sat evening | Prompt 5, deploy, incognito checks. Start the updated PDF from `docs/RESULTS.md` |
| Sat night | Sleep. Do not add features |
| Sun morning | Submit by 07:00, three hours before the deadline |

I have not verified any free host's current terms, so check limits and card requirements yourself before choosing one.
