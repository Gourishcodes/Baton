"""Prompt 5: UI verification and screenshot capture at 390w."""
import os
import sys
import time
from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BATON_URL", "http://localhost:8000")
SCREENSHOT_DIR = r"C:\Users\gouri\.gemini\antigravity\brain\0108573e-5c53-48f5-8a26-1556dbc8693b\screenshots"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

def run():
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 390, "height": 844})
        page = ctx.new_page()

        # 1. Sign up a new real user
        page.goto(f"{BASE_URL}/signup")
        email = f"real_user_{int(time.time())}@example.com"
        page.fill('input[name="name"]', "Jordan Lee")
        page.fill('input[name="email"]', email)
        page.fill('input[name="password"]', "longpassword123")
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")
        print(f"Signed up as {email}")

        # 2. Create a case
        page.goto(f"{BASE_URL}/cases")
        page.fill('input[name="name"]', "Beta Launch Handover")
        page.fill('input[name="leaver"]', "Sam Taylor")
        page.fill('input[name="successor"]', "Alex Morgan")
        page.fill('input[name="approver"]', "Jordan Lee")
        page.click('button:has-text("Create case")')
        page.wait_for_load_state("networkidle")
        print(f"Created case, at URL: {page.url}")

        # 3. Upload a couple sources
        with open("temp_team.txt", "w", encoding="utf-8") as f:
            f.write("Sam: Finalized the architecture\nAlex: Reviewed PR\n")
        page.set_input_files('input[name="file"]', "temp_team.txt")
        page.select_option('select[name="label"]', "team")
        page.click('button:has-text("Upload source")')
        page.wait_for_load_state("networkidle")

        with open("temp_tasks.csv", "w", encoding="utf-8") as f:
            f.write("Task,Assignee,Status\nT-101,Sam Taylor,Done\nT-102,,In Progress\n")
        page.set_input_files('input[name="file"]', "temp_tasks.csv")
        page.select_option('select[name="label"]', "team")
        page.click('button:has-text("Upload source")')
        page.wait_for_load_state("networkidle")

        # 4. Check Sources table at 390w
        # - SHA column hidden
        # - Seen by column shows only number, no "people" word
        seen_word_visible = page.locator(".seen-word").first.is_visible()
        print(f"Seen-word visible: {seen_word_visible} (expected False at 390w)")
        assert not seen_word_visible, "Word 'people' should be hidden at 390w"

        # Check scroll width vs client width (no horizontal overflow)
        body_scroll = page.evaluate("document.body.scrollWidth")
        inner_w = page.evaluate("window.innerWidth")
        print(f"body.scrollWidth: {body_scroll}, window.innerWidth: {inner_w}")
        assert body_scroll <= inner_w, f"Horizontal scroll overflow: {body_scroll} > {inner_w}"

        # 5. Build handover section has consent checkbox
        consent_cb = page.locator('input[name="consent"]')
        assert consent_cb.is_visible()

        # 6. Try to click Build handover with consent checked
        page.check('input[name="consent"]')
        page.click('button:has-text("Build handover")')
        time.sleep(1)
        page.wait_for_load_state("networkidle")

        # Plain message should be visible since no API key is configured
        print("Run status HTML:", page.locator('#run-status').inner_html())
        time.sleep(2)
        print("Run status HTML after 2s:", page.locator('#run-status').inner_html())
        content = page.content()
        assert "No API key is configured on the host. The demo case still works." in content
        print("Verified plain failure message banner when no API key configured.")

        # Capture 390w screenshot of Sources page
        out_path = os.path.join(SCREENSHOT_DIR, "real_account_sources_390w.png")
        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=out_path)
        print(f"Saved 390w screenshot to: {out_path}")

        # Cleanup temp files
        for p in ["temp_team.txt", "temp_tasks.csv"]:
            if os.path.exists(p):
                os.remove(p)

        browser.close()
        print("=== PROMPT 5 CHECKS COMPLETE ===")

if __name__ == "__main__":
    run()
