# Baton Round 3 spec

Applies on top of `docs/SPEC.md` and `docs/DESIGN.md`. Where they differ, this file wins for Round 3 work. It is written against the public repo at commit 4552a2a (main). Judging is on delivering exactly three things: bring your own data, real sign-in with an access log, and a permission-safe export. Nothing in this file adds a fourth.

## 0. What the code does today and what must change

| Where | Today | Round 3 |
|---|---|---|
| `app/middleware.py` `get_persona_from_request` | Returns `"lead"` (full clearance) when the cookie is missing or invalid | An anonymous or invalid session has no access: HTML routes redirect to `/login`, everything else returns the standard 404 |
| `app/middleware.py` `SECRET_KEY` | Falls back to a public string in the repo | The app refuses to start without `SECRET_KEY`, except under pytest |
| `app/main.py` `POST /role` | Anyone can become any role | Removed. Replaced by real sign-in (section 1) |
| `app/main.py` `upload_source` | Stub that redirects | Implemented (section 4) |
| `app/main.py` home, trust, demo routes | Case 1 is hard-coded | Case-aware. The demo case stays id 1 |
| `app/main.py` `demo_reset` | Deletes decisions, versions, acknowledgements and reopens gaps for every case | Scoped to case 1 only. Add a test with a second case present |
| `app/main.py` handlers | Names hard-coded ("Meera Joshi", "Divya Menon", "Arjun Rao") | Names come from the signed-in user |
| `app/db.py` | `sources.id` and `lines.line_id` are global primary keys | A second case would collide on `C1:004`. See section 4.2 |
| `app/access.py` | `visible(labels, persona)`, with roles read from `data/roles.json` | `visible(labels, principal)` with per-membership clearance. Fail-closed rules stay |
| `app/repo.py` | Every function takes `persona: str` | Takes a `Principal`. See section 1.3 |
| `app/middleware.py` wire check | Scans `text/html` only, and only for the 9 planted strings in `data/canaries.json` | Scans every textual response type, including exports. Section 3.4 |
| Render free tier | SQLite on ephemeral disk | Accounts and cases created on the host can vanish on restart. Disclose it. Seed the demo accounts and case at boot |

`access.visible` stays the single place that decides visibility. Templates and routes still never filter.

## 1. Real sign-in with per-person clearances (promise 2)

### 1.1 Data model (new tables, SQLite, stdlib only)

- `users(id, email UNIQUE, name, pw_hash, is_demo, created_at)`
- `sessions(token_hash PRIMARY KEY, user_id, created_at, expires_at)`
- `memberships(case_id, user_id, role, is_owner, labels JSON, added_by, added_at, PRIMARY KEY(case_id, user_id))`. `role` is one of `approver`, `leaver`, `successor`, `viewer`. `labels` is the person's clearance, a subset of `team`, `finance`, `hr`.
- `invites(token_hash PRIMARY KEY, case_id, role, labels JSON, created_by, created_at, expires_at, used_at)`
- `access_log(id, at, user_id, case_id, route, action, items JSON, labels_seen JSON)`. Never store claim or line text.
- `login_attempts(email, ip, at)` for throttling.

### 1.2 Authentication

- Email and password. Hash with `hashlib.scrypt` (per-user random salt, n=2**14, r=8, p=1). Minimum 10 characters. Compare in constant time. No new dependency.
- Session token: 32 random bytes, sent in an HttpOnly, SameSite=Lax cookie (add Secure when `X-Forwarded-Proto` is https). Only the SHA-256 of the token is stored. 12-hour expiry. `POST /logout` deletes the row.
- Login failures return one fixed message whether the email exists or not. Throttle: 5 failures per email and IP in 15 minutes, then a plain "Try again later".
- State-changing requests are rejected unless the `Origin` (or `Referer`) host matches `Host`.
- Sign-up is open at `/signup` (name, email, password). It is rate-limited per IP and capped by `MAX_USERS` (env, default 200). Accounts are not email-verified and there is no password reset. State both in the README and on the sign-up page.
- `POST /cases` and uploads require a non-demo account. Demo accounts only see case 1. The demo password is public, so letting demo accounts create cases would expose one judge's upload to the next.

### 1.3 Principal

```
Principal: user_id, name, email, case_id, role, is_owner, holds: frozenset[str]
```

- Built once per request by a dependency that reads the session, loads the membership for the `{case_id}` in the path, and returns the standard 404 if there is no membership. A user from case A requesting case B gets the same 404 body as a missing item.
- Every route under `/case/{id}/` uses that dependency. No route reads a cookie itself.
- Capabilities by role: `approver` decides claims and approves the handover; `leaver` and `approver` answer gaps; `successor` acknowledges; `viewer` reads. `is_owner` adds: upload sources, run extraction, manage members, view the access log, delete the case. Owner and approver are independent flags and roles.
- Keep a helper so existing tests can ask for the demo personas by their old names: `lead` is the approver and owner, `leaver`, `successor`, `finance_ops` (a viewer with team and finance).

### 1.4 Demo accounts

Seed four accounts at boot, members of case 1 only: Divya Menon (owner, approver; team, finance, hr), Meera Joshi (leaver; team), Arjun Rao (successor; team), Sameer Kulkarni (viewer; team, finance). The password is public, shown on the sign-in page, and read from `DEMO_PASSWORD` (default set in code, overridable). `is_demo = 1`.

The sign-in page lists them with one button each. Each button posts the real `/login` form with the published credentials, so the session and the access function are the real ones. While signed in as a demo user, the top bar keeps the three-button switch (Leaver, Approver, Successor) so the 20-second redaction moment still works. Each button performs a real sign-in. Everything else sees name, role, clearance and Sign out.

Keep `POST /role` (form field `role` = `leaver`, `lead`, `successor` or `finance_ops`) but change what it does: it creates a real session for the matching seeded demo account. It never accepts any other value and never touches a non-demo account. This keeps the protected `tests/test_design_rules.py` and `tests/test_drawer_centering.py` working unchanged, because they call `POST /role` before each visit. It is equivalent to signing in with the published demo password, so it adds no new exposure. Tests that currently forge the `baton_role` cookie (`tests/test_screens_flows.py`, `app/leaktest.py`) must instead use one helper, `login_as(client, name)`, that goes through the real login endpoint.

### 1.5 Invites

The owner creates an invite (role and clearance). Baton shows the one-time link once, `/invite/{token}`, valid 7 days. Opening it lets the person create an account (or sign in) and join with exactly that role and clearance. A used or expired token returns the standard 404. No email is sent.

### 1.6 Clearance changes

The owner can change a member's clearance or role. It takes effect on that person's next request, because the principal is rebuilt every request. The change is written to the access log.

### 1.7 Access log

- One row per response that delivered labelled items to a person: who, when, route, and the delivered items as `[{kind, id, labels}]`. No text. Item routes also log denied attempts (`action = "denied"`).
- Collect delivered items in the repository functions through a request-scoped collector, and write the row once after the response. Do not log one row per item.
- Exports are logged (`action = "export"`, with format and version).
- `/case/{id}/access`, owner only:
  - A question at the top: "Who has seen items labelled [hr]?" with a label select. The answer lists each person, how many times, and the last time.
  - Below it, the full log as a table: time, person, route, item count, labels.
- On the Sources screen the owner sees "Seen by N people" per source, from the same log.
- "Seen" means delivered in a response. It does not prove the person read it. Say so on the page.

### 1.8 Leak test as real accounts

`app/leaktest.py` signs in through the real login endpoint as each seeded account, then drives every view it already drives. Add: anonymous requests to every route (redirect or 404, never content); a tampered session cookie; an expired session; a member of case A requesting case B (same 404 body); a reused and an expired invite; a clearance downgrade taking effect on the next request; and the access-log page returning 404 to non-owners.

## 2. Single source for the handover

Create `build_handover(case_id, principal) -> dict` and move the section assembly out of `handover_view` into it. The HTML view, the Markdown export and the print page all call it. No other code builds handover content.

## 3. Permission-safe export (promise 3)

### 3.1 Routes

- `GET /case/{id}/handover.md` returns `text/markdown; charset=utf-8`, `Content-Disposition: attachment; filename="handover-{slug}-v{n}.md"`, and `X-Content-Type-Options: nosniff`.
- `GET /case/{id}/handover/print` returns an HTML page with a print stylesheet. A "Print or save as PDF" `.btn` calls `window.print()`. No PDF is generated on the server.
- Both exist only when an approved version exists; otherwise return the standard 404. Any member who can view the handover can export it. Each role gets its own file.

### 3.2 Content

- Same sections, same order, same approved-or-edited filter as the screen, from `build_handover`.
- Restricted items are absent. No redaction bars, no "N items hidden", no label names, in any export.
- Citations appear as line IDs only, for example `C1·026`. Never line text.
- Footer: handover version, approver name, approval time, the name of the person it was exported for, export time. No clearance or label names.
- Escape Markdown control characters in claim text. The print page relies on Jinja autoescape. Test both with a claim containing `<script>` and `[x](javascript:alert(1))`.

### 3.3 Print stylesheet

Append to `static/baton.css` under `/* additions */`: hide the bar, tabs, buttons and wire strip; keep the Newsreader column at 680 px; avoid page breaks inside an item; print the version footer. Tokens only.

### 3.4 Wire check and leak test

- The middleware scans every response whose type starts with `text/` or is `application/json`, not only `text/html`, and also checks the `Content-Disposition` filename.
- The leak test requests both exports for every account and scans them for every canary that account must not see. Positive controls: the approver's export contains the permitted canaries.
- Equivalence test: for each account, the set of claim IDs in the Markdown export equals the set on the handover page.

## 4. Bring your own data (promise 1)

### 4.1 Cases

`GET /cases` lists the signed-in person's cases. `POST /cases` creates one (name, leaver, successor, approver names), with the creator as owner and approver. A non-demo owner can delete a case and all its data (confirm first). Remove the hard-coded case 1 from home and Trust.

### 4.2 Source IDs

Demo IDs (`C1:026` and the rest) must not change. The answer key and tests use them. For new cases use globally unique source IDs with a case prefix, for example `B7C1`. Check every regex and parser that assumes the old shape before choosing the exact format, and add a test that two cases with the same file names cannot read each other's lines.

### 4.3 Upload (owner only)

- Kinds from extension: `.txt` chat, `.csv` tasks, `.md` notes. Label select: team, finance, hr.
- Limits: 5 sources per case, 200 KB per file, 1,500 non-blank lines per case, UTF-8 only. Reject binaries and NUL bytes. A CSV needs a header row and at most 30 columns.
- Every rejection is a plain `.banner` message that names the file and the reason. No stack traces.
- The "who owns this?" gap rule in `verify.py` should find the assignee and status columns case-insensitively by common names (assignee, owner, assigned to; status, state). If the columns are missing the rule is skipped.
- Show the numbered lines in the existing drawer so the owner can check what was read.

### 4.4 Live run

- "Build handover" runs `extract_for_case` on a background thread. The page shows plain progress (`Reading 2 of 3 label groups`) via HTMX polling and ends with counts.
- One call per distinct label set. Text under one label set never enters a call for another. Test it with a spy provider that records every prompt and asserts every line in a call belongs to sources whose label set equals that call's.
- Failure messages come from a fixed list ("The model did not answer in time. Your files are saved. Try again in a minute."). Catch everything at the route boundary and log details server side only.
- Cache live results in a runtime directory (not `demo_cache/`), keyed by the existing `compute_cache_key`, so re-running the same files costs no calls.
- Consent checkbox before the first run: "This text is sent to Google's Gemini API. The free tier may log inputs. I am not uploading confidential or personal data."
- Rate limits, all from env with defaults: 3 runs per user per day, 1 concurrent run per case, 4.5 seconds minimum between provider calls (15 per minute), `LIVE_DAILY_CALL_CAP` calls per day across the host, and `LIVE_ENABLED=0` as a kill switch. When a limit is hit, say which one, and say the demo still works.
- A new case's Claims screen carries a notice: "No accuracy figure exists for this case. Every claim is citation-checked by code and starts Pending."
- Do not change `PROMPT_VERSION`, the extraction prompt text, or `demo_cache/`.

### 4.5 Key policy (the open decision in the deck)

Default: a host key from `GEMINI_API_KEY`, behind the limits above, so a judge can try it without pasting a key. Optional later: a "use my own key" field, held in memory for that one run only, never written to the database, logs or the session. Do not build the optional field before everything else works.

### 4.6 Scan strings for new cases (stretch)

The planted canaries belong to the demo case. For a new case, derive scan strings from restricted lines (long numbers, amounts, emails, capitalised two-word names, ID-like tokens, and the full text of each restricted line), excluding any token that also appears in a line the viewer can see. Use them in the wire check and the leak test. If this is not built, the Trust page must say that for new cases only the access function protects restricted text, not the scan.

## 5. Interface additions

Use existing components. If one is missing, append it under `/* additions */` with tokens only and add it to `docs/design-reference.html`.

- **Sign-in.** A narrow `.wrap`, `.h1` "Sign in", two `.field`s, `.btn--primary`. Below it a `.banner` saying the demo accounts share a public password, then a `.tbl` of the four accounts (role, clearance as `.tag`s) with a `.btn` per row. No illustration and no social buttons. A link to Sign up.
- **Top bar.** Demo user: the three-button switch plus Sign out. Everyone else: name, role, clearance `.tag`s, Sign out (`.btn--quiet`). Replace the "Not authentication" line with "Demo accounts share a public password. Real accounts do not."
- **Members** (owner). A `.tbl` of members: name, role, clearance, last seen. Inline selects for role and labels. An invite form (role select, three label checkboxes, "Create invite link") that shows the link once in a `.banner`.
- **Access log** (owner). As in 1.7. No item text anywhere on the page.
- **Cases.** A `.tbl` (name, your role, status) and a "New case" form.
- **Sources.** Upload form with the limits written in `.meta`; per source: kind, label `.tag`, lines, "Seen by N people" for owners.
- **Run panel.** "Build handover" `.btn--primary`, the consent checkbox, a status line that updates in place.
- **Handover.** Two `.btn`s, "Download Markdown" and "Open print page", above the version footer.

## 6. Acceptance, in the wording of the promises

1. **Bring your own data:** an owner uploads a chat file, a CSV and notes under different labels, runs the pipeline, and gets claims that are citation-checked, labelled and conflict-checked. A spy test shows no text crosses label sets. A limit hit and a provider failure each show a plain message. The demo case still works afterwards.
2. **Real sign-in:** no route returns content to an anonymous request. Clearances are set per person by the owner. Every read goes through `access.visible`. The owner can answer "who has seen the HR notes?" from the access log. The leak test passes as real accounts across every view, including the negative cases in 1.8.
3. **Export:** each role downloads a different Markdown file and a print page built by `build_handover`. Footers carry version, approver and time. The wire check and the leak test cover every export for every role, with positive controls and an equivalence test.

## 7. Decisions for the owner of the project

1. Host key with caps as the default (4.5). Recommended, because a judge will not paste a key into a hackathon site.
2. Open sign-up with caps and no email verification (1.2). Recommended. Disclosed in the README.
3. Accept ephemeral storage on the free host and disclose it. The alternative is a paid disk or an external database, which this spec does not cover.

## 8. If time runs short

Cut in this order: 4.6 scan strings, the optional own-key field, the "Seen by N people" column, case deletion. Never cut the anonymous-fails-closed rule, the membership check on every case route, the spy test, the export scan, or the real-account leak test.
