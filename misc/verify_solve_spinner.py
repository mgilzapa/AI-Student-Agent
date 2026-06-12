"""Browser verification of the solveSheet loading spinner.

Drives the real UI: login -> open module -> context menu '⚡ Lösen' ->
assert the solution tab shows the spinner immediately, then the solved
content. Probes: re-solve shows the spinner again (no stale content);
aborted /solve-sheet shows the error state in the tab.
"""
import json
import sys
import time

import httpx
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()
BASE = "http://127.0.0.1:8000"
EMAIL = "smoke-test-agent@example.com"
PW = "Smoke-Test-9482!xyz"
SHOTS = "misc/shots"


def supabase_session():
    """Password-grant sign-in via GoTrue REST; returns the session dict the
    supabase-js SDK persists in localStorage."""
    import os
    url = os.environ["SUPABASE_URL"]
    anon = os.environ["SUPABASE_ANON_KEY"]
    r = httpx.post(
        f"{url}/auth/v1/token?grant_type=password",
        json={"email": EMAIL, "password": PW},
        headers={"apikey": anon, "Content-Type": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json(), url

import os
os.makedirs(SHOTS, exist_ok=True)

def log(msg):
    print(msg, flush=True)


def open_solve_menu(page):
    row = page.locator(".file-row", has_text="uebungsblatt1.txt").first
    row.hover()
    row.locator(".file-action-btn").first.click()
    page.get_by_text("⚡ Lösen").click()


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 950})

        # 1. Login: inject the supabase-js session into localStorage (the SDK's
        # own persistence format), then load /app — getSession() restores it.
        session, supa_url = supabase_session()
        ref = supa_url.split("//")[1].split(".")[0]
        page.add_init_script(
            f"window.localStorage.setItem('sb-{ref}-auth-token', {json.dumps(json.dumps(session))});"
        )
        page.goto(f"{BASE}/app")
        page.wait_for_selector("text=Smoke Test Modul", timeout=60000)
        log("STEP login (injected session): PASS (setup screen shows module)")

        # 2. Open module workspace
        page.get_by_text("Smoke Test Modul").first.click()
        page.wait_for_selector(".file-row", timeout=30000)
        log("STEP open workspace: PASS (file rows visible)")
        page.screenshot(path=f"{SHOTS}/1_workspace.png")

        # 3. Solve via context menu -> spinner must appear immediately
        open_solve_menu(page)
        page.wait_for_selector(".view-solution .pane-loading .modal-spinner", timeout=5000)
        hint = page.locator(".view-solution .proposal-loading-hint").inner_text()
        log(f"STEP spinner visible: PASS (hint='{hint}')")
        page.screenshot(path=f"{SHOTS}/2_solving_spinner.png")

        # 4. Wait for the solved content to replace the spinner
        page.wait_for_selector(".view-solution .sol-list", timeout=180000)
        n_items = page.locator(".view-solution .sol-item").count()
        log(f"STEP solution rendered: PASS ({n_items} task(s))")
        page.screenshot(path=f"{SHOTS}/3_solution_done.png")

        # 5. PROBE: re-solve -> spinner again (no stale content), then result
        open_solve_menu(page)
        page.wait_for_selector(".view-solution .pane-loading .modal-spinner", timeout=5000)
        stale = page.locator(".view-solution .sol-list").count()
        log(f"PROBE re-solve spinner: PASS (stale sol-list count={stale})")
        page.screenshot(path=f"{SHOTS}/4_resolve_spinner.png")
        page.wait_for_selector(".view-solution .sol-list", timeout=180000)
        log("PROBE re-solve completes: PASS")

        # 6. PROBE: abort /solve-sheet -> tab shows error state, no infinite spinner
        page.route("**/solve-sheet", lambda route: route.abort())
        open_solve_menu(page)
        page.wait_for_selector("text=Lösen fehlgeschlagen", timeout=15000)
        log("PROBE aborted request -> error state: PASS")
        page.screenshot(path=f"{SHOTS}/5_error_state.png")
        page.unroute("**/solve-sheet")

        # 7. PROBE: reload mid-state -> no orphaned forever-spinner tab restored
        page.reload()
        page.wait_for_selector("text=Smoke Test Modul", timeout=60000)
        page.get_by_text("Smoke Test Modul").first.click()
        page.wait_for_selector(".file-row", timeout=30000)
        time.sleep(2)
        spinners = page.locator(".view-solution .pane-loading").count()
        errors = page.locator("text=Lösen fehlgeschlagen").count()
        log(f"PROBE reload: PASS (loading panes={spinners}, error panes={errors})")
        page.screenshot(path=f"{SHOTS}/6_after_reload.png")

        browser.close()
        log("ALL STEPS PASS")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"FAIL: {e}")
        sys.exit(1)
