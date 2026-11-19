"""Leak test for Baton.

Run as: python -m app.leaktest

Implements docs/SPEC.md section 13:
- For every persona in data/roles.json and every view, fetch with a signed
  cookie via the FastAPI test client and scan the full response body for every
  canary whose group that persona does not hold.
- Also assert stubs match the fixed markup, successor handover has no label
  names, and tab/filter counts are consistent across personas with the same
  labels.
- Report a table (persona by view, count of forbidden canaries found) and the
  total. Report the count, never claim a guarantee.
"""
import html
import json
import os
import re
import sys
import tempfile

if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import secrets
if "SECRET_KEY" not in os.environ:
    os.environ["SECRET_KEY"] = secrets.token_hex(32)

from starlette.testclient import TestClient

from app import db, repo, extract, review, access
from app.auth import demo_principal, seed_demo_accounts, login_as
from app.main import app
from app.middleware import SECRET_KEY, CANARIES

# Fixed redacted markup (from SPEC section 7 / DESIGN.md section 5)
REDACTED_PATTERN = re.compile(
    r'<article\s+class="claim claim--redacted"\s+role="listitem">\s*'
    r'<span\s+class="type">&nbsp;</span>\s*'
    r'<div><div\s+class="redact redact--(a|b|c)"\s+role="img"\s+'
    r'aria-label="Item outside your clearance">Restricted</div></div>\s*'
    r'</article>'
)

# Views from SPEC section 13
VIEWS = [
    "/",
    "/case/1/sources",
    "/case/1/claims",
    "/case/1/claims?type=Decision",
    "/case/1/claims?type=Owner",
    "/case/1/claims?type=Risk",
    "/case/1/claims?type=Dependency",
    "/case/1/claims?type=OpenQuestion",
    "/case/1/claims?type=NextAction",
    "/case/1/claims?needs_review=1",
    "/case/1/gaps",
    "/case/1/conflicts",
    "/case/1/timeline",
    "/case/1/handover",
    "/case/1/handover.md",
    "/case/1/handover/print",
    "/case/1/approvals",
    "/trust",
]


def _parse_html_sections(html_text: str) -> dict[str, list[dict]]:
    sections = {}
    sec_matches = list(re.finditer(r'<h[23][^>]*>(.*?)</h[23]>', html_text))
    for i, m in enumerate(sec_matches):
        sec_title = m.group(1).strip()
        start = m.end()
        end = sec_matches[i+1].start() if i+1 < len(sec_matches) else len(html_text)
        sec_chunk = html_text[start:end]

        items = []
        p_matches = re.finditer(r'<p class="item item--(?:sourced|attested|inferred).*?>(.*?)</p>', sec_chunk, re.DOTALL)
        for pm in p_matches:
            content = pm.group(1)
            cites = [c.replace(":", "·") for c in re.findall(r'<a class="cite"[^>]*>(.*?)</a>', content)]
            clean = re.sub(r'<!--.*?-->', '', content, flags=re.DOTALL)
            clean = re.sub(r'<span[^>]*>.*?</span>', '', clean, flags=re.DOTALL)
            clean = re.sub(r'<a[^>]*>.*?</a>', '', clean, flags=re.DOTALL)
            clean = re.sub(r'<[^>]+>', '', clean)
            clean = html.unescape(clean)
            clean = " ".join(clean.split())
            items.append({"text": clean, "cites": cites})
        sections[sec_title] = items
    return sections


def _parse_md_sections(md_text: str) -> dict[str, list[dict]]:
    sections = {}
    sec_matches = list(re.finditer(r'^##[#]?\s+(.*?)$', md_text, re.MULTILINE))
    for i, m in enumerate(sec_matches):
        sec_title = m.group(1).strip()
        start = m.end()
        end = sec_matches[i+1].start() if i+1 < len(sec_matches) else len(md_text)
        sec_chunk = md_text[start:end]

        items = []
        for line in sec_chunk.splitlines():
            line = line.strip()
            if not line.startswith("- "):
                continue
            line_content = line[2:].strip()
            line_content = re.sub(r'<!--.*?-->', '', line_content).strip()
            cites = [c.replace(":", "·") for c in re.findall(r'\b[A-Z]\d+[·:]\d+\b', line_content)]
            for c in cites:
                line_content = line_content.replace(c, "").strip()
            line_content = re.sub(r'\[(Conflict|Sourced|Attested|Inferred)\]', '', line_content).strip()
            line_content = re.sub(r'\(Due [^\)]+\)', '', line_content).strip()
            line_content = re.sub(r'\(Superseded\)', '', line_content).strip()
            line_content = re.sub(r'\\([`*_{}\[\]#<>|~])', r'\1', line_content).strip()
            line_content = " ".join(line_content.split())
            items.append({"text": line_content, "cites": cites})
        sections[sec_title] = items
    return sections


def _set_persona(client, persona):
    """Sign in via the real login endpoint as persona."""
    login_as(client, persona)


_last_leak_results = None


def get_last_results():
    """Retrieve the cached results of the last leak test run, or run one."""
    global _last_leak_results
    if _last_leak_results is None:
        _last_leak_results = run_leak_test(verbose=False)
    return _last_leak_results


def run_leak_test(verbose: bool = True):
    """Execute the full leak test and return structured results.

    Returns dict with:
        total_found: int - total forbidden canaries found
        total_scanned: int - total checks performed
        rows: list[dict] - per persona/view results
        stub_failures: list[str] - stub markup violations
        label_failures: list[str] - label name leaks in successor handover
        count_mismatches: list[str] - tab/filter count inconsistencies
        positive_controls: list[dict] - canaries that SHOULD appear, did they?
    """
    # Set up an isolated DB
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    old_path = db.get_db_path() if db._db_path else None
    old_env_db = os.environ.get("BATON_DB")
    os.environ["BATON_DB"] = tmp.name
    db.set_db_path(tmp.name)

    try:
        case_id = db.load_demo_case()
        seed_demo_accounts()
        extract.extract_for_case(case_id, mode="cache_only")
        review.seed_demo_decisions(case_id)
        conn_init = db.get_conn()
        case_obj = conn_init.execute("SELECT approver FROM cases WHERE id = ?", (case_id,)).fetchone()
        approver = f"{case_obj['approver']} (seeded)" if case_obj and case_obj["approver"] else "Divya Menon (seeded)"
        review.create_version(case_id, approved_by=approver, seeded=True)

        client = TestClient(app)

        roles = access.get_roles()
        personas = list(roles.keys())

        # Build canary lookup: group -> [texts]
        canary_by_group = {}
        for c in CANARIES:
            canary_by_group.setdefault(c["group"], []).append(c["text"])

        results_rows = []
        total_found = 0
        total_scanned = 0
        stub_failures = []
        label_failures = []
        count_mismatches = []
        positive_controls = []

        # Get all claim IDs for drawer tests
        conn = db.get_conn()
        all_claim_ids = [
            r["id"] for r in conn.execute(
                "SELECT id FROM claims WHERE case_id = ?", (case_id,)
            ).fetchall()
        ]

        for persona in personas:
            _set_persona(client, persona)
            holds = access.persona_holds(persona)

            # Canaries this persona must NOT see
            forbidden = []
            for c in CANARIES:
                if c["group"] not in holds:
                    forbidden.append(c["text"])

            # Canaries this persona IS allowed to see (positive control)
            allowed = []
            for c in CANARIES:
                if c["group"] in holds:
                    allowed.append(c["text"])

            # Accumulate all response text seen by this persona for positive control checks
            persona_html = ""

            # 1. Scan all views (full page and HTMX partial)
            for view in VIEWS:
                resp = client.get(view)
                if resp.status_code != 404:
                    body = resp.text
                    persona_html += body
                    found_count = 0
                    for canary in forbidden:
                        total_scanned += 1
                        if canary in body:
                            found_count += 1
                            total_found += 1
                    results_rows.append({
                        "persona": persona,
                        "view": view,
                        "forbidden_count": len(forbidden),
                        "found": found_count,
                        "status_code": resp.status_code,
                    })

                # Also test HTMX partial for claims views
                if "/claims" in view:
                    htmx_resp = client.get(view, headers={"HX-Request": "true"})
                    if htmx_resp.status_code != 404:
                        hbody = htmx_resp.text
                        persona_html += hbody
                        h_found = 0
                        for canary in forbidden:
                            total_scanned += 1
                            if canary in hbody:
                                h_found += 1
                                total_found += 1
                        results_rows.append({
                            "persona": persona,
                            "view": f"{view} (HX)",
                            "forbidden_count": len(forbidden),
                            "found": h_found,
                            "status_code": htmx_resp.status_code,
                        })

            # 2. Drawer URLs for every claim
            for cid in all_claim_ids:
                resp = client.get(f"/case/1/claims/{cid}/source")
                body = resp.text
                persona_html += body
                found_count = 0
                for canary in forbidden:
                    total_scanned += 1
                    if canary in body:
                        found_count += 1
                        total_found += 1
                results_rows.append({
                    "persona": persona,
                    "view": f"/case/1/claims/{cid}/source",
                    "forbidden_count": len(forbidden),
                    "found": found_count,
                    "status_code": resp.status_code,
                })

            # 3. Hidden claim 404 vs made-up ID 404 must be identical
            principal = demo_principal(persona, case_id=case_id)
            claims_data = repo.get_claims(case_id, principal)
            hidden_ids = [c["id"] for c in claims_data if not c.get("visible", True)]
            if hidden_ids:
                made_up_resp = client.get("/case/1/claims/99999/source")
                for hid in hidden_ids[:3]:  # test a few
                    hidden_resp = client.get(f"/case/1/claims/{hid}/source")
                    if hidden_resp.text != made_up_resp.text:
                        stub_failures.append(
                            f"Hidden claim {hid} drawer body differs from 404 for {persona}"
                        )

            # 4. Stub checks on claims page
            claims_resp = client.get("/case/1/claims")
            body = claims_resp.text
            # Every redacted article must match fixed markup
            redacted_articles = re.findall(
                r'<article\s+class="claim claim--redacted".*?</article>',
                body, re.DOTALL
            )
            for i, art in enumerate(redacted_articles):
                if not REDACTED_PATTERN.search(art):
                    stub_failures.append(
                        f"Redacted stub #{i} for {persona} does not match fixed markup"
                    )
                # Must not contain label names, basis, type, cites
                art_lower = art.lower()
                for label_word in ["finance", "team"]:
                    if label_word in art_lower and label_word not in ("restricted",):
                        # "team" can appear inside "item" etc, check properly
                        if f'>{label_word}<' in art_lower or f' {label_word} ' in art_lower:
                            stub_failures.append(
                                f"Redacted stub for {persona} contains label name '{label_word}'"
                            )
                if re.search(r'\bhr\b', art, re.IGNORECASE):
                    # Check it's not just part of "href" etc
                    if re.search(r'(?<![a-z])hr(?![a-z])', art, re.IGNORECASE):
                        # Further filter: allow "href" but not standalone "HR"
                        cleaned = re.sub(r'href', '', art, flags=re.IGNORECASE)
                        if re.search(r'(?<![a-z])hr(?![a-z])', cleaned, re.IGNORECASE):
                            stub_failures.append(
                                f"Redacted stub for {persona} contains label name 'HR'"
                            )

            # 5. Successor handover: no label names
            if persona == "successor":
                ho_resp = client.get("/case/1/handover")
                ho_body = ho_resp.text
                if "<main" in ho_body:
                    main_body = ho_body.split("<main")[1].split("</main>")[0]
                else:
                    main_body = ho_body
                if "finance" in main_body.lower():
                    label_failures.append("Successor handover contains 'finance'")
                # Check for "HR" as a label name (not substring of href etc)
                # Remove href, then check for standalone HR
                cleaned_main = re.sub(r'href', '', main_body, flags=re.IGNORECASE)
                if re.search(r'(?<![a-z])hr(?![a-z])', cleaned_main, re.IGNORECASE):
                    label_failures.append("Successor handover contains 'HR' as label name")
                if 'class="tag' in main_body:
                    label_failures.append("Successor handover contains tag elements")

            # 6. Answer-gap endpoint for hidden gaps
            principal = demo_principal(persona, case_id=case_id)
            gaps_data = repo.get_gaps(case_id, principal)
            all_gaps = conn.execute(
                "SELECT id FROM gaps WHERE case_id = ?", (case_id,)
            ).fetchall()
            visible_gap_ids = {g["id"] for g in gaps_data}
            hidden_gap_ids = [g["id"] for g in all_gaps if g["id"] not in visible_gap_ids]
            if hidden_gap_ids:
                made_up_gap_resp = client.post(
                    "/case/1/gaps/99999/answer",
                    data={"answer": "test", "type": "Decision", "label": "team"},
                )
                for hgid in hidden_gap_ids[:2]:
                    hidden_gap_resp = client.post(
                        f"/case/1/gaps/{hgid}/answer",
                        data={"answer": "test", "type": "Decision", "label": "team"},
                    )
                    if hidden_gap_resp.text != made_up_gap_resp.text:
                        stub_failures.append(
                            f"Hidden gap {hgid} answer response differs from 404 for {persona}"
                        )

            # 7. Positive control: canaries a persona IS allowed to see DO appear
            if persona == "lead":
                for canary_text in allowed:
                    present = canary_text in persona_html
                    pc = {
                        "persona": persona,
                        "canary": canary_text,
                        "present": present,
                    }
                    positive_controls.append(pc)
            elif persona == "finance_ops":
                for canary_text in allowed:
                    present = canary_text in persona_html
                    pc = {
                        "persona": persona,
                        "canary": canary_text,
                        "present": present,
                    }
                    positive_controls.append(pc)

        # 8. Tab count and filter-chip consistency
        # Personas with the same labels should see the same tab counts
        holds_map = {}
        for persona in personas:
            hkey = tuple(sorted(access.persona_holds(persona)))
            holds_map.setdefault(hkey, []).append(persona)

        for hkey, same_personas in holds_map.items():
            if len(same_personas) < 2:
                continue
            ref_persona = same_personas[0]
            _set_persona(client, ref_persona)
            ref_resp = client.get("/case/1/claims")
            ref_nums = re.findall(r'<span class="num">(\d+)</span>', ref_resp.text)

            for other_persona in same_personas[1:]:
                _set_persona(client, other_persona)
                other_resp = client.get("/case/1/claims")
                other_nums = re.findall(r'<span class="num">(\d+)</span>', other_resp.text)
                if ref_nums != other_nums:
                    count_mismatches.append(
                        f"Tab/filter counts differ between {ref_persona} ({ref_nums}) "
                        f"and {other_persona} ({other_nums})"
                    )

        # 9. Approver export positive controls (ROUND3_SPEC 3.4)
        _set_persona(client, "lead")
        lead_md = client.get("/case/1/handover.md").text
        lead_print = client.get("/case/1/handover/print").text
        for canary in CANARIES:
            if canary["text"] in lead_md:
                positive_controls.append({
                    "persona": "lead (.md export)",
                    "canary": canary["text"],
                    "present": True,
                })
            if canary["text"] in lead_print:
                positive_controls.append({
                    "persona": "lead (print export)",
                    "canary": canary["text"],
                    "present": True,
                })

        # Positive controls for finance_ops exports (approved finance canaries)
        _set_persona(client, "finance_ops")
        finance_md = client.get("/case/1/handover.md").text
        finance_print = client.get("/case/1/handover/print").text
        approved_finance_canaries = ["₹3,18,400", "7.4 months", "₹24,750", "₹19,200"]
        for c_text in approved_finance_canaries:
            if c_text in finance_md:
                positive_controls.append({
                    "persona": "finance_ops (.md export)",
                    "canary": c_text,
                    "present": True,
                })
            if c_text in finance_print:
                positive_controls.append({
                    "persona": "finance_ops (print export)",
                    "canary": c_text,
                    "present": True,
                })

        # 10. Equivalence test: compare ordered claim text plus citation ids per section
        equivalence_failures = []
        canonical_sections = ["Who owns what", "What is at risk", "What depends on what", "What is open", "What to do first", "Decisions and why", "History"]
        for persona in personas:
            _set_persona(client, persona)
            ho_text = client.get("/case/1/handover").text
            md_text = client.get("/case/1/handover.md").text

            ho_secs = _parse_html_sections(ho_text)
            md_secs = _parse_md_sections(md_text)

            for sec_name in canonical_sections:
                h_items = ho_secs.get(sec_name, [])
                m_items = md_secs.get(sec_name, [])
                if h_items != m_items:
                    equivalence_failures.append(
                        f"Section '{sec_name}' mismatch for {persona}: {h_items} on page vs {m_items} in markdown"
                    )

        # 11. Escaping test: claim containing <script> and [x](javascript:alert(1))
        escaping_failures = []
        xss_text = "Check <script>alert(1)</script> and [x](javascript:alert(1)) payload"
        run = repo.get_latest_run(case_id)
        run_id = run["id"] if run else 1
        cur = conn.execute(
            """INSERT INTO claims (case_id, run_id, type, text, basis, labels, flags, identity)
               VALUES (?, ?, 'Decision', ?, 'sourced', '["team"]', '{}', 'xss_test:C1:026')""",
            (case_id, run_id, xss_text)
        )
        xss_cid = cur.lastrowid
        conn.execute("INSERT INTO claim_cites (claim_id, line_id, valid) VALUES (?, 'C1:026', 1)", (xss_cid,))
        conn.execute(
            "INSERT OR REPLACE INTO decisions (identity, case_id, decision, by, at, seeded) VALUES ('xss_test:C1:026', ?, 'approved', 'Divya Menon', ?, 1)",
            (case_id, db.now_iso())
        )
        conn.commit()

        _set_persona(client, "lead")
        test_md = client.get("/case/1/handover.md").text
        test_print = client.get("/case/1/handover/print").text

        if "<script>" in test_md:
            escaping_failures.append("Markdown export contains unescaped <script> tag")
        if "[x](javascript:alert(1))" in test_md:
            escaping_failures.append("Markdown export contains unescaped [x](javascript:alert(1)) link")
        if "<script>" in test_print:
            escaping_failures.append("Print export contains unescaped <script> tag")

        # Cleanup test claim
        conn.execute("DELETE FROM claim_cites WHERE claim_id = ?", (xss_cid,))
        conn.execute("DELETE FROM claims WHERE id = ?", (xss_cid,))
        conn.execute("DELETE FROM decisions WHERE identity = 'xss_test:C1:026' AND case_id = ?", (case_id,))
        conn.commit()

        global _last_leak_results
        _last_leak_results = {
            "total_found": total_found,
            "total_scanned": total_scanned,
            "rows": results_rows,
            "stub_failures": stub_failures,
            "label_failures": label_failures,
            "count_mismatches": count_mismatches,
            "positive_controls": positive_controls,
            "equivalence_failures": equivalence_failures,
            "escaping_failures": escaping_failures,
        }
        return _last_leak_results

    finally:
        db.close()
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
        if old_path:
            db.set_db_path(old_path)
        else:
            db.reset()
        if old_env_db:
            os.environ["BATON_DB"] = old_env_db
        elif "BATON_DB" in os.environ:
            del os.environ["BATON_DB"]


def format_report(results: dict) -> str:
    """Format the leak test report as a string."""
    lines = []
    lines.append("=" * 70)
    lines.append("BATON: LEAK TEST REPORT")
    lines.append("=" * 70)

    # Summary
    lines.append(
        f"\nStrings scanned: {results['total_scanned']}"
    )
    lines.append(
        f"Forbidden strings found: {results['total_found']}"
    )

    # Per-persona summary
    persona_totals = {}
    for row in results["rows"]:
        p = row["persona"]
        persona_totals.setdefault(p, {"scanned": 0, "found": 0})
        persona_totals[p]["scanned"] += row["forbidden_count"]
        persona_totals[p]["found"] += row["found"]

    lines.append(f"\n{'Persona':<15} {'Views':>6} {'Scanned':>8} {'Found':>6}")
    lines.append("-" * 40)
    for persona, totals in persona_totals.items():
        view_count = sum(1 for r in results["rows"] if r["persona"] == persona)
        lines.append(
            f"{persona:<15} {view_count:>6} {totals['scanned']:>8} {totals['found']:>6}"
        )

    # Views with findings
    findings = [r for r in results["rows"] if r["found"] > 0]
    if findings:
        lines.append(f"\nFindings ({len(findings)} views with leaks):")
        for f in findings:
            lines.append(f"  {f['persona']:>15} {f['view']:<50} found={f['found']}")
    else:
        lines.append(f"\nNo forbidden canary strings found in any view.")

    # Stub check
    lines.append(f"\nStub markup checks: {len(results['stub_failures'])} failures")
    for f in results["stub_failures"]:
        lines.append(f"  - {f}")

    # Label leaks
    lines.append(f"Successor label checks: {len(results['label_failures'])} failures")
    for f in results["label_failures"]:
        lines.append(f"  - {f}")

    # Count mismatches
    lines.append(f"Tab/filter count consistency: {len(results['count_mismatches'])} mismatches")
    for f in results["count_mismatches"]:
        lines.append(f"  - {f}")

    # Equivalence checks
    eq_failures = results.get("equivalence_failures", [])
    lines.append(f"Equivalence checks: {len(eq_failures)} failures")
    for f in eq_failures:
        lines.append(f"  - {f}")

    # Escaping checks
    esc_failures = results.get("escaping_failures", [])
    lines.append(f"Escaping checks: {len(esc_failures)} failures")
    for f in esc_failures:
        lines.append(f"  - {f}")

    # Positive controls
    pc = results.get("positive_controls", [])
    if pc:
        present = sum(1 for p in pc if p["present"])
        lines.append(f"\nPositive controls: {present} of {len(pc)} expected canaries present")
        for p in pc:
            mark = "+" if p["present"] else "-"
            lines.append(f"  [{mark}] {p['persona']}: canary present={p['present']}")

    lines.append("")
    lines.append("=" * 70)
    lines.append("A strong check, not a proof.")
    lines.append("=" * 70)

    return "\n".join(lines)


if __name__ == "__main__":
    results = run_leak_test(verbose=True)
    print(format_report(results))
    failures = (
        results["total_found"]
        + len(results.get("stub_failures", []))
        + len(results.get("label_failures", []))
        + len(results.get("count_mismatches", []))
        + len(results.get("equivalence_failures", []))
        + len(results.get("escaping_failures", []))
    )
    sys.exit(0 if failures == 0 else 1)
