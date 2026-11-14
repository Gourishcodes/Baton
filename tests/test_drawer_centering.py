"""Playwright test: drawer opens on ?open_cite=, gives srcline--cited, and centers line vertically."""
import os
import pytest
from playwright.sync_api import sync_playwright

BASE = os.environ.get("BATON_URL", "http://localhost:8000")


def test_drawer_opens_and_centers_cited_line():
    """Open /case/1/claims?open_cite=C1:026 as lead, assert the C1:026 line has
    class srcline--cited and is inside the drawer's visible area.
    """
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        # Switch to lead persona
        ctx.request.post(BASE + "/role", form={"role": "lead"})

        page = ctx.new_page()
        page.goto(BASE + "/case/1/claims?open_cite=C1:026")
        page.wait_for_selector("#drawer")
        page.wait_for_timeout(300)

        # 1. Assert C1:026 line exists and has class srcline--cited
        line_el = page.locator("#line-C1-026")
        assert line_el.count() == 1, "Element #line-C1-026 not found in drawer"
        classes = line_el.get_attribute("class") or ""
        assert "srcline--cited" in classes.split(), f"Expected srcline--cited class, got: {classes}"

        # 2. Assert line is inside the drawer's visible area
        drawer = page.locator("#drawer")
        drawer_box = drawer.bounding_box()
        assert drawer_box is not None, "Drawer bounding box not available"

        header = page.locator("#drawer header")
        header_box = header.bounding_box()

        line_box = line_el.bounding_box()
        assert line_box is not None, "Line bounding box not available"

        # Check line is below drawer header (not occluded)
        if header_box:
            assert line_box["y"] >= header_box["y"] + header_box["height"], (
                f"Line top ({line_box['y']}) is occluded by drawer header ({header_box['y'] + header_box['height']})"
            )

        # Check line is within drawer top and bottom
        assert line_box["y"] >= drawer_box["y"], "Line is above drawer top"
        assert line_box["y"] + line_box["height"] <= drawer_box["y"] + drawer_box["height"], (
            "Line is below drawer bottom"
        )

        # Assert line is approximately centered vertically in the drawer's scroll viewport
        visible_center_y = drawer_box["y"] + (drawer_box["height"] / 2)
        line_center_y = line_box["y"] + (line_box["height"] / 2)
        assert abs(line_center_y - visible_center_y) < 150, (
            f"Line is not vertically centered: line center={line_center_y}, drawer center={visible_center_y}"
        )

        ctx.close()
        browser.close()
