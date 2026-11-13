"""Design rules, enforced on computed styles in a real browser.

Run against the live app:
    BATON_URL=http://localhost:8000 pytest tests/test_design_rules.py -q
Run against the static reference page:
    pytest tests/test_design_rules.py -q

Routes are read from ROUTES below; edit if routes change. The persona cookie is
set by calling POST /role before each visit, so every persona is audited.
"""
import os
import re
import pathlib
import pytest
from playwright.sync_api import sync_playwright

BASE = os.environ.get("BATON_URL")
REF = pathlib.Path(__file__).resolve().parent.parent / "docs" / "design-reference.html"

ROUTES = ["/", "/case/1/sources", "/case/1/claims", "/case/1/gaps", "/case/1/conflicts",
          "/case/1/timeline", "/case/1/handover", "/case/1/approvals", "/trust"]
PERSONAS = ["leaver", "lead", "successor"]

ALLOWED_FONTS = {"IBM Plex Sans", "IBM Plex Mono", "Newsreader"}
HEX = ["#F5F5F2", "#FFFFFF", "#151512", "#45453E", "#6B6B63", "#DADAD2",
       "#1D6B47", "#8F5A00", "#C2301B", "#FFE98A"]
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐✨✅❌]")

JS = r"""
() => {
  const out = [];
  const px = v => parseFloat(v) || 0;
  for (const el of document.querySelectorAll('body, body *')) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || el.closest('script,style,svg')) continue;
    const id = el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).join('.') : '');
    if (cs.backgroundImage !== 'none') out.push(['gradient-or-image', id, cs.backgroundImage.slice(0, 60)]);
    if (cs.boxShadow !== 'none') out.push(['shadow', id, cs.boxShadow.slice(0, 60)]);
    if (cs.filter !== 'none' || (cs.backdropFilter && cs.backdropFilter !== 'none')) out.push(['filter-or-blur', id, cs.filter + ' ' + cs.backdropFilter]);
    for (const k of ['borderTopLeftRadius','borderTopRightRadius','borderBottomLeftRadius','borderBottomRightRadius']) {
      const v = cs[k];
      if (v.includes('%') || px(v) > 2) { out.push(['radius', id, v]); break; }
    }
    const fam = cs.fontFamily.split(',')[0].replace(/["']/g, '').trim();
    if (el.childNodes.length && Array.from(el.childNodes).some(n => n.nodeType === 3 && n.textContent.trim()))
      out.push(['font', id, fam]);
    out.push(['color', id, cs.color]);
    if (cs.backgroundColor !== 'rgba(0, 0, 0, 0)') out.push(['bg', id, cs.backgroundColor]);
    for (const side of ['Top','Right','Bottom','Left']) {
      if (px(cs['border'+side+'Width']) > 0 && cs['border'+side+'Style'] !== 'none') out.push(['border', id, cs['border'+side+'Color']]);
    }
  }
  out.push(['scroll', 'document', document.documentElement.scrollWidth + '/' + window.innerWidth]);
  out.push(['text', 'body', document.body.innerText]);
  return out;
}
"""


def _rgb(h):
    h = h.lstrip("#")
    return "rgb(%d, %d, %d)" % tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


ALLOWED_RGB = {_rgb(h) for h in HEX}


def audit(page):
    problems = []
    for kind, where, val in page.evaluate(JS):
        if kind in ("gradient-or-image", "shadow", "filter-or-blur", "radius"):
            problems.append(f"{kind}: <{where}> {val}")
        elif kind == "font" and val not in ALLOWED_FONTS:
            problems.append(f"font: <{where}> uses {val!r}")
        elif kind == "border" and val == "rgba(0, 0, 0, 0)":
            continue  # transparent border (inactive tab underline, uncited source line) is allowed
        elif kind in ("color", "bg", "border") and val not in ALLOWED_RGB:
            problems.append(f"{kind} outside palette: <{where}> {val}")
        elif kind == "scroll":
            sw, iw = map(int, val.split("/"))
            if sw > iw:
                problems.append(f"horizontal scroll: page is {sw}px wide in a {iw}px window")
        elif kind == "text" and EMOJI.search(val):
            problems.append("emoji found in page text")
    # collapse duplicates but keep order
    seen, uniq = set(), []
    for p in problems:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def _visit(browser, url, viewport, persona=None):
    ctx = browser.new_context(viewport=viewport)
    if persona and BASE:
        ctx.request.post(BASE + "/role", form={"role": persona})
    page = ctx.new_page()
    page.goto(url)
    page.wait_for_timeout(300)
    return ctx, page


def _targets():
    if BASE:
        return [(BASE + r, p) for r in ROUTES for p in PERSONAS]
    return [(REF.as_uri(), None)]


@pytest.mark.parametrize("viewport", [{"width": 1440, "height": 900}, {"width": 390, "height": 844}],
                         ids=["desktop", "phone"])
def test_design_rules(viewport):
    failures = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for url, persona in _targets():
            ctx, page = _visit(browser, url, viewport, persona)
            for p in audit(page):
                failures.append(f"{url} [{persona}] {p}")
            ctx.close()
        browser.close()
    assert not failures, "\n" + "\n".join(failures[:40])
