"""Headless browser test: open the app, navigate to PS module, click roadmap row, see what appears."""
from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8001/"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    page = ctx.new_page()

    console_msgs = []
    page.on("console", lambda m: console_msgs.append(f"[{m.type}] {m.text}"))
    page.on("pageerror", lambda e: console_msgs.append(f"[PAGEERROR] {e}"))

    page.goto(URL, wait_until="networkidle")
    print("=== After load ===")
    print("title:", page.title())

    # Use module name "ps2" (the one in registry)
    ps = page.get_by_text("ps2", exact=False).first
    print("ps2 element exists:", ps.count())
    page.wait_for_timeout(400)
    if ps.count() > 0:
        ps.click()
        page.wait_for_timeout(1500)
    else:
        # Try direct JS call to open the module
        print("clicking via JS")
        page.evaluate("typeof openModule === 'function' && openModule('ps2')")
        page.wait_for_timeout(1500)

    # Look for roadmap row
    rm = page.locator(".file-row.is-roadmap")
    print("roadmap row count:", rm.count())
    if rm.count() > 0:
        rm.first.click()
        page.wait_for_timeout(1500)

    # Walk up the layout tree and report each ancestor's box
    print("=== layout walk ===")
    boxes = page.evaluate(r"""() => {
      const out = [];
      const sels = ['.view-pane.view-roadmap', '.ws-viewport', '.pane', '.ws-center',
                    '.ws-shell', '.workspace', '.ws-frame', '.ws-grid', 'body'];
      for (const s of sels) {
        const el = document.querySelector(s);
        if (!el) { out.push({sel:s, found:false}); continue; }
        const r = el.getBoundingClientRect();
        const cs = getComputedStyle(el);
        out.push({sel:s, x:r.x, y:r.y, w:r.width, h:r.height,
                  display:cs.display, position:cs.position,
                  gridRow:cs.gridRow, flex:cs.flex,
                  minHeight:cs.minHeight});
      }
      return out;
    }""")
    for b in boxes:
        print(b)

    # Inspect roadmap-body specifically
    rb = page.evaluate(r"""() => {
      const el = document.querySelector('.roadmap-body');
      if (!el) return null;
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      return {w:r.width, h:r.height, display:cs.display,
              gridTemplateRows:cs.gridTemplateRows,
              gridTemplateColumns:cs.gridTemplateColumns,
              flex:cs.flex, minHeight:cs.minHeight,
              parentClass: el.parentElement && el.parentElement.className};
    }""")
    print("roadmap-body:", rb)

    pl = page.evaluate(r"""() => {
      const el = document.querySelector('#rm-phaselist');
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return {w:r.width, h:r.height, childCount:el.children.length};
    }""")
    print("rm-phaselist:", pl)

    # Detailed children of .pane
    children = page.evaluate(r"""() => {
      const pane = document.querySelector('.pane');
      if (!pane) return null;
      const cs = getComputedStyle(pane);
      const out = {pane: {gridTemplateRows: cs.gridTemplateRows, height: pane.getBoundingClientRect().height}};
      out.children = Array.from(pane.children).map(c => {
        const r = c.getBoundingClientRect();
        const cc = getComputedStyle(c);
        return {tag: c.tagName, cls: c.className, w: r.width, h: r.height, display: cc.display};
      });
      return out;
    }""")
    print("pane children:", children)

    print("\n=== Console messages ===")
    for m in console_msgs:
        print(m)

    page.screenshot(path="C:/tmp/roadmap-debug.png", full_page=True)
    print("screenshot -> C:/tmp/roadmap-debug.png")

    browser.close()
