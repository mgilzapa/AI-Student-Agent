"""Headless layout check for the mobile workspace fixes (no auth needed).

Extracts the real CSS from app/static/index.html, rebuilds the workspace DOM
structure, and measures the computed layout at a phone viewport to confirm:
  1. The center pane's viewport row is NOT collapsed (PDF / generated files area
     has real height).
  2. The chat pane stacks topbar / messages / footer vertically (grid, column),
     and only the chat is visible when data-mobile-pane="right".
"""
import re, sys, pathlib
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = ROOT / "app" / "static" / "index.html"

html = INDEX.read_text(encoding="utf-8")
css = re.search(r"<style>(.*?)</style>", html, re.S).group(1)

# Minimal reproduction of the real workspace DOM (classes/ids matter for CSS).
harness = f"""<!doctype html><html><head><meta charset="utf-8">
<style>{css}</style>
<style>html,body{{margin:0}} #workspace-screen{{height:100vh;display:flex;flex-direction:column;opacity:1}}</style>
</head>
<body data-theme="light">
<section class="screen" id="workspace-screen">
  <div class="ws-grid" id="ws-grid" data-mobile-pane="center">
    <aside class="ws-left" id="ws-rail"><div style="flex:1">LEFT</div></aside>
    <section class="ws-center" id="ws-center">
      <div class="pane" data-pane-idx="0">
        <div class="tab-bar"><button class="tab active"><span class="tab-name">file.pdf</span></button></div>
        <div class="view-bar hidden"></div>
        <div class="ws-viewport">
          <div class="view-pane view-pdf active">
            <div class="pdf-frame-slot" style="flex:1;width:100%;height:100%;background:#1a1a1a;"></div>
          </div>
        </div>
      </div>
    </section>
    <div class="ws-split" id="ws-split-right"></div>
    <aside class="ws-right">
      <div class="chat-topbar"><div><h3>Chat</h3><p id="chat-subtitle">sub</p></div>
        <button class="chat-close-btn" id="toggle-right">x</button></div>
      <div id="chat"><div class="chat-empty"><div class="chat-empty-inner"><strong>Bereit</strong></div></div></div>
      <div class="chat-footer"><div class="chat-form"><textarea id="input" rows="1"></textarea></div></div>
    </aside>
  </div>
  <nav class="ws-mobile-nav" id="ws-mobile-nav">
    <button data-pane="left">Menu</button><button data-pane="center">Inhalt</button>
    <button data-pane="right">Chat</button>
  </nav>
</section>
</body></html>"""

out = ROOT / "misc" / "_mobile_harness.html"
out.write_text(harness, encoding="utf-8")

errors = []
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 390, "height": 844})
    pg.goto(out.as_uri())

    # ── Check 1: center pane visible, viewport + slot have real height ──
    pg.eval_on_selector("#ws-grid", "el => el.dataset.mobilePane = 'center'")
    slot = pg.eval_on_selector(".pdf-frame-slot", "el => el.getBoundingClientRect().height")
    vp   = pg.eval_on_selector(".ws-viewport", "el => el.getBoundingClientRect().height")
    centerDisp = pg.eval_on_selector(".ws-center", "el => getComputedStyle(el).display")
    print(f"[center] display={centerDisp}  viewport.h={vp:.0f}  slot.h={slot:.0f}")
    if slot < 200:
        errors.append(f"center pane content collapsed (slot height {slot:.0f}px, expected >200)")

    # ── Check 2: chat pane stacks vertically and others are hidden ──
    pg.eval_on_selector("#ws-grid", "el => el.dataset.mobilePane = 'right'")
    rightDisp  = pg.eval_on_selector(".ws-right", "el => getComputedStyle(el).display")
    centerVis  = pg.eval_on_selector(".ws-center", "el => getComputedStyle(el).display")
    leftVis    = pg.eval_on_selector(".ws-left", "el => getComputedStyle(el).display")
    topbarTop  = pg.eval_on_selector(".chat-topbar", "el => el.getBoundingClientRect().top")
    chatTop    = pg.eval_on_selector("#chat", "el => el.getBoundingClientRect().top")
    footerTop  = pg.eval_on_selector(".chat-footer", "el => el.getBoundingClientRect().top")
    chatBox    = pg.eval_on_selector("#chat", "el => el.getBoundingClientRect().height")
    print(f"[chat] right.display={rightDisp} center={centerVis} left={leftVis}")
    print(f"[chat] topbar.top={topbarTop:.0f} chat.top={chatTop:.0f} footer.top={footerTop:.0f} chat.h={chatBox:.0f}")
    if rightDisp != "grid":
        errors.append(f"chat pane display is '{rightDisp}', expected 'grid'")
    if not (topbarTop < chatTop < footerTop):
        errors.append("chat children are not stacked vertically (topbar < chat < footer failed)")
    if centerVis != "none" or leftVis != "none":
        errors.append(f"other panes still visible while chat open (center={centerVis}, left={leftVis})")
    if chatBox < 200:
        errors.append(f"chat message area collapsed ({chatBox:.0f}px)")

    b.close()

out.unlink(missing_ok=True)
if errors:
    print("\nFAIL:")
    for e in errors:
        print("  -", e)
    sys.exit(1)
print("\nPASS: mobile workspace layout correct")
