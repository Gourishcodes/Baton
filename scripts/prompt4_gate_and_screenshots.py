"""Prompt 4 Gate execution and screenshot capture."""
import io
import os
import sys
import time
from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BATON_URL", "http://localhost:8000")
SCREENSHOT_DIR = r"C:\Users\gouri\.gemini\antigravity\brain\0108573e-5c53-48f5-8a26-1556dbc8693b\screenshots"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

def run():
    print("=== GATE START ===")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()

        # -------------------------------------------------------------
        # Part 1: Gate
        # "Create case as fresh sign-up, upload CSV with headers Task, Owner, State, see parsed;
        #  upload PDF renamed .txt and read message."
        # -------------------------------------------------------------
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        # 1. Fresh sign-up
        signup_url = f"{BASE_URL}/signup"
        print(f"Navigating to {signup_url}")
        page.goto(signup_url)
        fresh_email = f"gate_user_{int(time.time())}@example.com"
        page.fill('input[name="name"]', "Gate User")
        page.fill('input[name="email"]', fresh_email)
        page.fill('input[name="password"]', "securepassword123")
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")
        print(f"Signed up as {fresh_email} and redirected to: {page.url}")

        # 2. Go to /cases and create case
        page.goto(f"{BASE_URL}/cases")
        page.fill('input[name="name"]', "Gate Handover Case")
        page.fill('input[name="leaver"]', "Sarah Chen")
        page.fill('input[name="successor"]', "Dave Miller")
        page.fill('input[name="approver"]', "Gate User")
        page.click('button:has-text("Create case")')
        page.wait_for_load_state("networkidle")
        case_url = page.url
        print(f"Created case, redirected to Sources page: {case_url}")
        assert "/sources" in case_url

        # 3. Upload CSV with headers Task, Owner, State -> see parsed
        csv_file_path = "temp_gate_tasks.csv"
        csv_content = "Task,Owner,State\nMigrate Gateway,Sarah Chen,In Progress\nAudit Invoices,,To Do\n"
        with open(csv_file_path, "w", encoding="utf-8") as f:
            f.write(csv_content)

        page.set_input_files('input[name="file"]', csv_file_path)
        page.select_option('select[name="label"]', "finance")
        page.click('button:has-text("Upload source")')
        page.wait_for_load_state("networkidle")
        print("Uploaded CSV with headers 'Task, Owner, State'")
        assert "temp_gate_tasks.csv" in page.content()
        assert "finance" in page.content()
        print("Verified CSV source listed on Sources page.")

        # Click on source or drawer to see parsed lines
        page.click('a:has-text("temp_gate_tasks.csv")')
        page.wait_for_selector("#drawer")
        page.wait_for_timeout(300)
        drawer_text = page.locator("#drawer").inner_text()
        print("Drawer content for parsed CSV:")
        for line in drawer_text.splitlines()[:6]:
            print(f"  {line}")
        assert "Task=Migrate Gateway" in drawer_text
        assert "State=In Progress" in drawer_text
        print("Verified CSV successfully parsed into numbered lines!")

        # 4. Upload PDF renamed .txt and read message
        fake_pdf_path = "temp_renamed_pdf.txt"
        with open(fake_pdf_path, "wb") as f:
            f.write(b"%PDF-1.4 binary fake content \x00\x01\x02")

        page.set_input_files('input[name="file"]', fake_pdf_path)
        page.select_option('select[name="label"]', "team")
        page.click('button:has-text("Upload source")')
        page.wait_for_load_state("networkidle")
        banner_text = page.locator(".banner").inner_text()
        print(f"Uploaded PDF renamed .txt -> Banner message displayed: '{banner_text}'")
        assert "temp_renamed_pdf.txt: Binary PDF files are not allowed." in banner_text

        # Clean up temp gate files
        os.remove(csv_file_path)
        os.remove(fake_pdf_path)
        print("=== GATE PASSED ===")

        # -------------------------------------------------------------
        # Part 2: Three synthetic files under three labels & Screenshots
        # "Upload three synthetic files under three labels to a new case.
        #  Screenshot the Sources page with numbered lines showing in the drawer at 1440w and 390w."
        # -------------------------------------------------------------
        print("\n=== SYNTHETIC CASE & SCREENSHOTS ===")
        page.goto(f"{BASE_URL}/cases")
        page.fill('input[name="name"]', "Platform Engineering Handover")
        page.fill('input[name="leaver"]', "Sarah Chen")
        page.fill('input[name="successor"]', "Dave Miller")
        page.fill('input[name="approver"]', "Gate User")
        page.click('button:has-text("Create case")')
        page.wait_for_load_state("networkidle")
        case_sources_url = page.url
        print(f"Created synthetic case: {case_sources_url}")

        # Synthetic File 1: chat.txt (team)
        chat_path = "chat.txt"
        with open(chat_path, "w", encoding="utf-8") as f:
            f.write(
                "2026-03-01 09:30 Sarah Chen: Starting the migration handover for payment service\n"
                "2026-03-01 09:32 Dave Miller: Got it, all credentials and API tokens are in vault\n"
                "2026-03-01 09:35 Sarah Chen: Deployment pipeline is configured in GitHub Actions\n"
                "2026-03-01 09:40 Dave Miller: We will run dry-run tests on staging first\n"
            )
        page.set_input_files('input[name="file"]', chat_path)
        page.select_option('select[name="label"]', "team")
        page.click('button:has-text("Upload source")')
        page.wait_for_load_state("networkidle")
        print("Uploaded 1/3: chat.txt under team")

        # Synthetic File 2: tasks.csv (finance)
        tasks_path = "tasks.csv"
        with open(tasks_path, "w", encoding="utf-8") as f:
            f.write(
                "Task,Owner,State\n"
                "Migrate Payment Gateway,Sarah Chen,In Progress\n"
                "Reconcile Ledger Balances,,In Progress\n"
                "Audit Vendor Invoices,Dave Miller,Done\n"
                "Update Banking Webhooks,,To Do\n"
            )
        page.set_input_files('input[name="file"]', tasks_path)
        page.select_option('select[name="label"]', "finance")
        page.click('button:has-text("Upload source")')
        page.wait_for_load_state("networkidle")
        print("Uploaded 2/3: tasks.csv under finance")

        # Synthetic File 3: notes.md (hr)
        notes_path = "notes.md"
        with open(notes_path, "w", encoding="utf-8") as f:
            f.write(
                "# Compensation and Performance Notes\n"
                "- Annual performance reviews completed for Q1 engineering team\n"
                "- Discretionary retention bonuses allocated for core infrastructure leads\n"
                "- Health insurance transition deadline is end of month\n"
            )
        page.set_input_files('input[name="file"]', notes_path)
        page.select_option('select[name="label"]', "hr")
        page.click('button:has-text("Upload source")')
        page.wait_for_load_state("networkidle")
        print("Uploaded 3/3: notes.md under hr")

        # Clean up local files
        os.remove(chat_path)
        os.remove(tasks_path)
        os.remove(notes_path)

        # Open drawer on tasks.csv (c...s2) to show numbered lines in drawer
        page.click('a:has-text("tasks.csv")')
        page.wait_for_selector("#drawer")
        page.wait_for_timeout(400)

        # Desktop screenshot (1440w)
        page.set_viewport_size({"width": 1440, "height": 900})
        page.wait_for_timeout(300)
        desktop_png = os.path.join(SCREENSHOT_DIR, "sources_page_with_drawer_1440w.png")
        page.screenshot(path=desktop_png)
        print(f"Captured 1440w screenshot: {desktop_png}")

        # Mobile screenshot (390w)
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(300)
        mobile_png = os.path.join(SCREENSHOT_DIR, "sources_page_with_drawer_390w.png")
        page.screenshot(path=mobile_png)
        print(f"Captured 390w screenshot: {mobile_png}")

        ctx.close()
        browser.close()
        print("=== ALL COMPLETED SUCCESSFULLY ===")

if __name__ == "__main__":
    run()
