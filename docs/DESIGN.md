# Baton design rules

`docs/SPEC.md` says visual rules live here and in `static/baton.css`. This file is the written form of what `static/baton.css` and `docs/design-reference.html` already show. If this file and `static/baton.css` disagree, the CSS wins. If the CSS and the reference page disagree, fix the CSS.

Ignore the folder `design_reference/`. It is an earlier attempt (Archivo font, different class names). Do not read it, copy from it or import from it.

## 1. The idea in one sentence

Baton is a case file, not a dashboard. The page is a ledger of claims. Every claim carries a bar on its left edge that says how we know it, and a row of source IDs that opens the exact line. What the viewer is not cleared for is drawn as a black bar by the server, with nothing behind it.

## 2. What we are replacing

The default for this kind of product is: left sidebar, stat cards across the top, rounded cards with coloured pill badges, indigo-to-purple gradient accents, Inter, dark mode, a drag-and-drop upload hero, chat bubbles, toasts and spinners. None of that appears anywhere in Baton. If a screen you are building starts to look like that, stop and re-read section 4.

## 3. Tokens (already in `static/baton.css`, never add to them without a reason)

| Token | Value | Used for |
|---|---|---|
| `--paper` | #F5F5F2 | page background |
| `--surface` | #FFFFFF | drawer, inputs, hovered claim |
| `--ink` | #151512 | text, borders, primary buttons, Sourced bar |
| `--ink-2` / `--ink-3` | #45453E / #6B6B63 | secondary text / metadata |
| `--rule` | #DADAD2 | hairlines |
| `--attested` | #1D6B47 | Attested only |
| `--inferred` | #8F5A00 | Inferred only |
| `--conflict` | #C2301B | Conflict and Reject only |
| `--mark` | #FFE98A | the cited source line, and a hovered cite chip |

Type: IBM Plex Sans for the interface, IBM Plex Mono for IDs, labels, numbers and anything machine-made, Newsreader only inside `.doc` (the handover, because that is the one thing people read start to finish). Three families, no others. Fonts are self-hosted in `static/fonts/`. No CDN at runtime.

Shape: no gradients, no shadows, no blur, border radius 0 (2px on form fields and filter chips only), borders 1px. Spacing steps are `--s1`..`--s16` (4, 8, 12, 16, 24, 32, 48, 64 px). Do not invent other values.

## 4. Hard rules

1. Write no new CSS unless a component truly does not exist. If you must add one, append it at the very bottom of `static/baton.css` under a new comment line `/* additions */`, change nothing that is already in the file, use tokens only, and add it to `docs/design-reference.html` in the same change.
2. Every screen is built from the classes that exist. Copy markup from `docs/design-reference.html`. Put repeated markup in Jinja macros (`claim`, `cite`, `tag`, `status`, `redacted_claim`, `srcline`, `wire`) in `templates/_macros.html`, once.
3. Evidence basis is shown three ways at once: the left bar pattern (solid, double, dashed, solid red, dotted), the colour, and the word. Never colour alone.
4. Review status is a small square plus a word (`.status--approved|edited|rejected|pending`). Not a pill, not a badge.
5. Labels (Team, Finance, HR) use `.tag`. A restricted label uses `.tag--restricted` (black fill). A viewer who is not cleared never sees a label name; see section 6.
6. No emoji. No icon font. No icon library. The only graphic is the wordmark SVG in the top bar.
7. No toasts, no modals, no spinners, no skeleton shimmer, no progress bars with animated stripes. Feedback is an inline `.banner` or a changed row. Long jobs say what they are doing in words ("Reading 2 of 3 label groups") and update with HTMX.
8. Only one animation exists: `draw`, the 180 ms redaction bar wipe, already in the CSS and switched off for `prefers-reduced-motion`. Do not add hover transitions, fades or slides.
9. Copy: sentence case, plain words, no exclamation marks, no "AI-powered", no "Oops", no "Let's". Say what happened and what the person can do. Never call anything "verified".
10. Layout: one column, maximum content width 1240 px (`.wrap`), the handover text column 680 px (`.doc`). No sidebar. Navigation is the tab strip.

## 5. Screens

All screens share: `.bar` (wordmark, case name, persona radiogroup, "Role switch for the demo. Not authentication."), `.tabs` with counts in `.num`, an `.h1` and one `.meta` line, then the content, then `.wire` at the foot. Take every screen's markup from the reference page.

**Home `/`.** Not a landing page. One `.h1` line, then the three sentences from the Round 1 plan as plain paragraphs: "Every claim shows how we know it. Anything we do not know, we ask the leaver. Anyone who should not see it, does not." Then two buttons: "Open the demo case" (`.btn--primary`, posts `/demo/open`) and "Start a new case" (`.btn`). Under them a `.banner`: synthetic data only, free-tier models may log inputs, role switch is not authentication. Nothing else. A judge must be inside the product after one click.

**Sources.** A `.tbl` of the sources (ID, file, kind, label `.tag`, lines, hash prefix). Upload form below: file input, label select, `.btn`. Parse errors show in a `.banner` naming the line and the reason.

**Claims (the main screen).**
- Legend row (`.legend`) top right of the heading, filters (`.filters`) below it.
- `.ledger` of `article.claim.claim--{basis}`. Left column 96 px: the type in `.type`. Right: the sentence (`.text`), an optional `.note`, then `.rowfoot`: basis word, label `.tag`, `.status`, `.cites`, `.acts`.
- States: `claim--superseded` (grey text, note says what replaced it), `claim--rejected` (strikethrough, only Restore offered), `claim--conflict`, `claim--redacted`.
- Clicking a `.cite` opens the source drawer (`.drawer`) on the right and scrolls to the cited line, which has `.srcline--cited` (yellow `--mark`). `.main-with-drawer` keeps the list from being covered. Drawer footer: "Line IDs are fixed when the file is read: C1·026 always means this line."
- Keyboard for the approver: J/K move, A approve, E edit, R reject, Enter opens the source. Show the `.kbdhint` line. About 40 lines of vanilla JS in `static/baton.js`, no libraries.
- Only the approver sees Approve, Edit, Reject. Other personas see the status only.
- Redacted claim markup is exactly this, copied from the reference page: `<article class="claim claim--redacted" role="listitem"><span class="type">&nbsp;</span><div><div class="redact redact--a" role="img" aria-label="Item outside your clearance">Restricted</div></div></article>`. The width class (`redact--a|b|c`) is chosen by the item's position modulo 3, never by text length. No type, no basis, no label name, no ID, no cites. See SPEC section 7.
- A footer line under the ledger: "N items are outside your clearance. Ask the approver, {name}." N is the only number disclosed.

**Gaps.** One `.gap` per gap: the question (`.q`), the one-line reason (`.meta`), the cited lines as `.quote`, then a `.field` and a label select (`label.lbl`), type select, and "Record answer" (`.btn--primary`). An answered gap shows the resulting claim with `.attestation` (double green bar) and the sentence "Attested claims do not go stale automatically: there is no source line to compare."

**Conflicts.** Each conflict is two `.claim--conflict` rows in a `.cols` grid, the same label `.tag` under both, and the note "Baton does not pick one. The approver decides." A cross-label conflict the viewer is not cleared for is not listed at all; it is part of the "N items outside your clearance" count.

**Timeline.** `ul.tl`: date in `.d` (mono, right aligned), a rail, the claim sentence with its cites. Dated claims only, sorted ascending, superseded ones greyed.

**Handover.** `.doc` in Newsreader. Section order is fixed by SPEC section 8. Each item is a `p.item` with its left bar by basis, the sentence, `.tag`, `.cite` chips. Restricted items are a `.redact` bar inside `.item--redacted`. At the foot: "Approved by {name}, version {n}, {time}." and, for the successor, "Acknowledge receipt" (`.btn--primary`). After acknowledging, the button becomes the sentence "Acknowledged by {name}, {time}. Case closed."

**Approvals.** `.tbl`: version, approver, time, claim count, seeded yes/no. Newest first.

**Trust.** Left: `.counter` (96 px mono) with `.counter-label` "Planted strings found in views the role may not see". Right: `.tbl` of measures (recall, claims matching a key fact, citation validity, and the seven traps with pass or fail). Below: buttons "Run leak test" and "Run accuracy check" that stream rows into the table. Always print the two honest lines: "A strong check, not a proof." and "Synthetic data written by the team with Claude. Small sample. Not a benchmark." Numbers come from running the code, never from the template.

## 6. The one memorable moment

Switching the persona from Approver to Successor redraws the same list with black bars where Finance and HR items were. The server draws the bars, and the text behind them was never sent. The `.wire` strip at the foot of every page says how many planted strings it scanned for and that zero are present. A judge should see this in the first 20 seconds of the demo. To make that happen: `/demo/open` lands on `/case/1/claims` as Approver with the drawer already open on C1·026, and the page's `.meta` line says "Switch to Successor to see what they see."

## 7. Responsive and accessible

- At 900 px and below the claim grid becomes one column, the drawer becomes a bottom sheet (60vh), the `.notauth` line and case name hide. At 1100 px and below the drawer no longer reserves space. All of this is already in the CSS. Check at 390 px wide that nothing scrolls sideways.
- Focus ring is `:focus-visible` 2 px ink. Keep it. Skip link stays. Persona switch is a `role="radiogroup"`. The selected claim has `aria-selected="true"`.
- Body text 14/20, never below 11 px (mono labels). Contrast, measured against `--paper` (WCAG ratio): `--ink` 16.8, `--ink-2` 8.8, `--ink-3` 4.9, `--attested` 5.9, `--inferred` 5.3, `--conflict` 5.2. All pass 4.5:1, so any token pairing in the file is safe on `--paper` or `--surface`. Do not introduce a lighter grey.
- The page must work with JavaScript off for reading and for form posts. HTMX and the keyboard layer only enhance.

## 8. How to check your work

1. Serve the app and run `BATON_URL=http://localhost:8000 pytest tests/test_design_rules.py -q`. It audits computed styles for gradients, shadows, radius, fonts, colours outside the tokens, emoji and horizontal scroll, for every persona and route.
2. Take screenshots at 1440 and 390 wide of every route as Approver and as Successor. Put `docs/design-reference.html` next to them and fix differences in spacing, type and bars before moving on.
3. If you added a component, show it in `docs/design-reference.html` too.
