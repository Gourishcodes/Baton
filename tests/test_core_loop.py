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
