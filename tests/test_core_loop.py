"""End-to-end test: the full core loop from the perspective of a stranger.

Acts like a stranger who has never seen the product:
1. Open the home page
2. Click "Open the demo case"
3. Switch to Successor and assert the handover shows version 1 but not the pending claims
4. Switch to Leaver and answer one gap
5. Switch to Approver and approve that claim and the handover
6. Switch to Successor, acknowledge, assert the case is Closed.

No test depends on a live model.
"""
import os
import urllib.request
import pytest
from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BATON_URL", "http://localhost:8000")


@pytest.fixture(autouse=True)
def reset_demo_state():
    """Ensure the database starts in a clean seeded demo state."""
    req = urllib.request.Request(f"{BASE_URL}/demo/reset", data=b"", method="POST")
    try:
        urllib.request.urlopen(req)
        from app import db
        conn = db.get_conn()
        conn.execute("DELETE FROM login_attempts")
        conn.commit()
    except Exception as e:
        pytest.fail(f"Could not reset demo at {BASE_URL}: {e}")


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def test_core_loop(browser):
    """Full demo loop as a stranger would experience it using UI clicks."""
    # Reset demo at start so it passes on any starting state
    req = urllib.request.Request(f"{BASE_URL}/demo/reset", data=b"", method="POST")
    try:
        urllib.request.urlopen(req)
    except Exception as e:
        pytest.fail(f"Could not reset demo at {BASE_URL}: {e}")

    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()

    # 1. Open home page
    page.goto(f"{BASE_URL}/")
    page.wait_for_load_state("networkidle")
    assert "Baton" in page.title() or "Baton" in page.content()

    # 2. Click "Open the demo case"
    demo_btn = page.locator('button:has-text("Open the demo case"), input[value="Open the demo case"]').first
    demo_btn.click()
    page.wait_for_load_state("networkidle")
    assert "/case/1/claims" in page.url

    # 3. Switch to Successor and assert handover shows version 1 but not pending claims
    # Click Successor persona button in the header
    page.locator('.persona button[value="successor"]').click()
    page.wait_for_load_state("networkidle")

    # Navigate to Handover tab
    page.locator('.tabs a[href*="/handover"]').click()
    page.wait_for_load_state("networkidle")

    # Assert handover shows version 1
    content = page.content().lower()
    assert "version 1" in content or "v1" in content

    # Assert pending claims do NOT appear
    # The pending pilot conflict and unapproved claims do not show as active claims
    doc_text = page.locator(".doc").inner_text()
    assert "Pending" not in doc_text

    # Successor must see no label names in handover
    assert "tag--restricted" not in page.content()

    # 4. Switch to Leaver and answer one gap
    page.locator('.persona button[value="leaver"]').click()
    page.wait_for_load_state("networkidle")

    # Navigate to Gaps tab
    page.locator('.tabs a[href*="/gaps"]').click()
    page.wait_for_load_state("networkidle")

    # Find the first gap's answer form and type an answer
    answer_box = page.locator("textarea").first
    answer_box.fill("Tanvi Shetty will cover on-call support during the initial pilot phase.")
    page.locator('button:has-text("Record answer")').first.click()
    page.wait_for_load_state("networkidle")

    # 5. Switch to Approver (lead)
    page.locator('.persona button[value="lead"]').click()
    page.wait_for_load_state("networkidle")

    # Navigate to Claims tab and filter by needs review
    page.locator('.tabs a[href*="/claims"]').click()
    page.wait_for_load_state("networkidle")

    needs_review_filter = page.locator('.filters button:has-text("Needs review")').first
    needs_review_filter.click()
    page.wait_for_load_state("networkidle")

    # Approve the newly created attested claim
    approve_btn = page.locator('article.claim--attested button:has-text("Approve"), article button.btn--primary:has-text("Approve")').first
    approve_btn.click()
    page.wait_for_load_state("networkidle")

    # Navigate to Handover tab and approve handover
    page.locator('.tabs a[href*="/handover"]').click()
    page.wait_for_load_state("networkidle")

    approve_ho_btn = page.locator('button:has-text("Approve handover")').first
    approve_ho_btn.click()
    page.wait_for_load_state("networkidle")

    # Assert new version was created
    assert "version 2" in page.content().lower()

    # 6. Switch to Successor, acknowledge, assert case is Closed
    page.locator('.persona button[value="successor"]').click()
    page.wait_for_load_state("networkidle")

    # Click Acknowledge receipt
    ack_btn = page.locator('button:has-text("Acknowledge receipt")').first
    ack_btn.click()
    page.wait_for_load_state("networkidle")

    # Assert case is Closed
    handover_content = page.content()
    assert "Case closed." in handover_content or "case closed" in handover_content.lower()

    context.close()


def test_demo_accounts_login_no_cookies(browser):
    """Prompt 6 item 8: Run with no cookies: sign in as each demo account through the real form."""
    demo_accounts = [
        ("divya@example.com", "Divya Menon", "Approver"),
        ("meera@example.com", "Meera Joshi", "Leaver"),
        ("arjun@example.com", "Arjun Rao", "Successor"),
        ("sameer@example.com", "Sameer Kulkarni", "Viewer"),
    ]

    for email, name, role in demo_accounts:
        # Fresh context with no cookies
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        page.goto(f"{BASE_URL}/login")
        page.wait_for_load_state("networkidle")

        page.locator('form[action="/login"] input[type="email"]').fill(email)
        page.locator('form[action="/login"] input[type="password"]').fill("baton-demo-2026")
        page.locator('form[action="/login"] button.btn--primary').click()
        page.wait_for_load_state("networkidle")

        assert "/case/1/claims" in page.url or "/cases" in page.url
        body_text = page.content()
        assert role in body_text or name in body_text

        context.close()


def test_new_user_live_run_and_successor_export(browser, tmp_path):
    """Prompt 6 item 8: New user signs up, creates case, uploads 3 sources under different labels,
    runs live extract with the spy provider, approves one claim, downloads the successor export,
    verifies restricted lines are absent."""
    import secrets
    import re
    suffix = secrets.token_hex(4)
    owner_email = f"owner_playwright_{suffix}@example.com"
    owner_password = "password12345"

    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()

    # 1. Sign up as new user
    page.goto(f"{BASE_URL}/signup")
    page.wait_for_load_state("networkidle")
    page.locator('input[name="name"]').fill("Playwright Owner")
    page.locator('input[name="email"]').fill(owner_email)
    page.locator('input[name="password"]').fill(owner_password)
    page.locator('button:has-text("Create account")').click()
    page.wait_for_load_state("networkidle")

    # 2. Create case
    if "/cases" not in page.url:
        page.goto(f"{BASE_URL}/cases")
        page.wait_for_load_state("networkidle")

    page.locator('input[name="name"]').fill(f"Service Handover {suffix}")
    page.locator('input[name="leaver"]').fill("Alice Engineer")
    page.locator('input[name="successor"]').fill("Bob Successor")
    page.locator('input[name="approver"]').fill("Playwright Owner")
    page.locator('button:has-text("Create case")').click()
    page.wait_for_load_state("networkidle")

    # Determine created case ID from URL
    match_cid = re.search(r"/case/(\d+)", page.url)
    assert match_cid is not None, f"Expected case URL, got {page.url}"
    cid = int(match_cid.group(1))

    # 3. Upload 3 sources under different labels
    page.goto(f"{BASE_URL}/case/{cid}/sources")
    page.wait_for_load_state("networkidle")

    fin_token = f"FIN_SECRET_{secrets.token_hex(4)}"
    hr_token = f"HR_SECRET_{secrets.token_hex(4)}"

    # Source 1: Team chat
    team_file = tmp_path / "team.txt"
    team_file.write_text("All services migrated to primary cluster\nMonitoring dashboards active", encoding="utf-8")
    page.locator('#source_file').set_input_files(str(team_file))
    page.locator('#source_label').select_option("team")
    page.locator('button:has-text("Upload source")').click()
    page.wait_for_load_state("networkidle")

    # Source 2: Finance notes
    fin_file = tmp_path / "finance.txt"
    fin_file.write_text(f"Vendor contract budget capped at 3 lakh {fin_token}\nPayment schedule Q3", encoding="utf-8")
    page.locator('#source_file').set_input_files(str(fin_file))
    page.locator('#source_label').select_option("finance")
    page.locator('button:has-text("Upload source")').click()
    page.wait_for_load_state("networkidle")

    # Source 3: HR notes
    hr_file = tmp_path / "hr.txt"
    hr_file.write_text(f"Confidential staffing review {hr_token}\nSalary adjustments list", encoding="utf-8")
    page.locator('#source_file').set_input_files(str(hr_file))
    page.locator('#source_label').select_option("hr")
    page.locator('button:has-text("Upload source")').click()
    page.wait_for_load_state("networkidle")

    # 4. Build handover with consent
    page.locator('input[name="consent"]').check()
    page.locator('button:has-text("Build handover")').click()

    # Wait for HTMX polling to finish
    page.wait_for_selector('text=Handover built:', timeout=45000)

    # 5. Go to Claims tab and approve one claim
    page.goto(f"{BASE_URL}/case/{cid}/claims")
    page.wait_for_load_state("networkidle")

    approve_btn = page.locator('article.claim button:has-text("Approve")').first
    approve_btn.click()
    page.wait_for_load_state("networkidle")

    # Approve handover
    page.goto(f"{BASE_URL}/case/{cid}/handover")
    page.wait_for_load_state("networkidle")
    page.locator('button:has-text("Approve handover")').click()
    page.wait_for_load_state("networkidle")

    # 6. Create an invite for Successor with team clearance only
    page.goto(f"{BASE_URL}/case/{cid}/members")
    page.wait_for_load_state("networkidle")

    # Fill invite form
    page.locator('form[action*="/invites"] select[name="role"]').select_option("successor")
    # Make sure team is checked, finance and hr are unchecked
    for lbl in ["finance", "hr"]:
        chk = page.locator(f'form[action*="/invites"] input[type="checkbox"][name="labels"][value="{lbl}"]')
        if chk.is_checked():
            chk.uncheck()
    team_chk = page.locator('form[action*="/invites"] input[type="checkbox"][name="labels"][value="team"]')
    if not team_chk.is_checked():
        team_chk.check()

    page.locator('form[action*="/invites"] button:has-text("Create invite link")').click()
    page.wait_for_load_state("networkidle")

    # Extract invite link or token
    invite_link = page.locator('a[href*="/invite/"]').first
    invite_url = invite_link.get_attribute("href")
    assert "/invite/" in invite_url
    if invite_url.startswith("/"):
        invite_url = f"{BASE_URL}{invite_url}"

    context.close()

    # 7. Successor opens invite with no cookies, joins case, and downloads export
    succ_context = browser.new_context(viewport={"width": 1440, "height": 900})
    succ_page = succ_context.new_page()

    succ_page.goto(invite_url)
    succ_page.wait_for_load_state("networkidle")

    succ_email = f"successor_playwright_{suffix}@example.com"
    succ_page.locator('form[action="/signup"] input[name="name"]').fill("Bob Successor")
    succ_page.locator('form[action="/signup"] input[name="email"]').fill(succ_email)
    succ_page.locator('form[action="/signup"] input[name="password"]').fill("password12345")
    succ_page.locator('form[action="/signup"] button:has-text("Create account & join")').click()
    succ_page.wait_for_load_state("networkidle")

    # Successor requests handover markdown export
    md_resp = succ_page.request.get(f"{BASE_URL}/case/{cid}/handover.md")
    assert md_resp.status == 200
    export_md = md_resp.text()

    # 8. Verify restricted lines/tokens are absent, approved team claim is present
    assert fin_token not in export_md, f"Restricted finance token leaked in successor export: {export_md}"
    assert hr_token not in export_md, f"Restricted HR token leaked in successor export: {export_md}"
    assert "budget capped" not in export_md
    assert "salary adjustments" not in export_md
    assert "migrated to primary cluster" in export_md or "All services migrated" in export_md

    succ_context.close()
