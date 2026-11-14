"""Capture screenshots for Prompt 3 verification."""
import os
import pathlib
from playwright.sync_api import sync_playwright

OUT_DIR = pathlib.Path(r"C:\Users\gouri\.gemini\antigravity\brain\0108573e-5c53-48f5-8a26-1556dbc8693b\screenshots")
OUT_DIR.mkdir(parents=True, exist_ok=True)

BASE = "http://localhost:8000"

def capture():
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        
        # 1. Approver (lead) at 1440w with drawer open on C1:026
        ctx_lead_desk = browser.new_context(viewport={"width": 1440, "height": 900})
        ctx_lead_desk.request.post(BASE + "/role", form={"role": "lead"})
        page_lead_desk = ctx_lead_desk.new_page()
        page_lead_desk.goto(BASE + "/case/1/claims?open_cite=C1:026")
        page_lead_desk.wait_for_timeout(500)
        lead_desk_path = OUT_DIR / "approver_1440w.png"
        page_lead_desk.screenshot(path=str(lead_desk_path), full_page=True)
        print("Saved:", lead_desk_path)
        ctx_lead_desk.close()

        # 2. Approver (lead) at 390w
        ctx_lead_mob = browser.new_context(viewport={"width": 390, "height": 844})
        ctx_lead_mob.request.post(BASE + "/role", form={"role": "lead"})
        page_lead_mob = ctx_lead_mob.new_page()
        page_lead_mob.goto(BASE + "/case/1/claims")
        page_lead_mob.wait_for_timeout(500)
        lead_mob_path = OUT_DIR / "approver_390w.png"
        page_lead_mob.screenshot(path=str(lead_mob_path), full_page=True)
        print("Saved:", lead_mob_path)
        ctx_lead_mob.close()

        # 3. Successor at 1440w
        ctx_succ_desk = browser.new_context(viewport={"width": 1440, "height": 900})
        ctx_succ_desk.request.post(BASE + "/role", form={"role": "successor"})
        page_succ_desk = ctx_succ_desk.new_page()
        page_succ_desk.goto(BASE + "/case/1/claims")
        page_succ_desk.wait_for_timeout(500)
        succ_desk_path = OUT_DIR / "successor_1440w.png"
        page_succ_desk.screenshot(path=str(succ_desk_path), full_page=True)
        print("Saved:", succ_desk_path)
        ctx_succ_desk.close()

        # 4. Successor at 390w
        ctx_succ_mob = browser.new_context(viewport={"width": 390, "height": 844})
        ctx_succ_mob.request.post(BASE + "/role", form={"role": "successor"})
        page_succ_mob = ctx_succ_mob.new_page()
        page_succ_mob.goto(BASE + "/case/1/claims")
        page_succ_mob.wait_for_timeout(500)
        succ_mob_path = OUT_DIR / "successor_390w.png"
        page_succ_mob.screenshot(path=str(succ_mob_path), full_page=True)
        print("Saved:", succ_mob_path)
        ctx_succ_mob.close()

        browser.close()

if __name__ == "__main__":
    capture()
