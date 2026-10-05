"""Drive the web UI in headless Chromium: a diagram must appear early, the
badge must report progress while searching, and the run must end on a
guarantee.  Usage: python bench/ui_anytime_test.py schema.sql out_dir [port]"""
import sys
import time

from playwright.sync_api import sync_playwright

ddl = open(sys.argv[1]).read()
out = sys.argv[2]
port = sys.argv[3] if len(sys.argv) > 3 else "8766"
with sync_playwright() as p:
    b = p.chromium.launch(args=["--disable-gpu"])
    pg = b.new_page(viewport={"width": 1500, "height": 950})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(f"http://127.0.0.1:{port}/")
    pg.fill("#schema", ddl)
    pg.select_option("#time", "120")
    t0 = time.time()
    pg.click("#go")
    pg.wait_for_selector("#stage svg", timeout=60000)
    print(f"first diagram after {time.time() - t0:.1f}s; badge: {pg.inner_text('#badge')[:140]}")
    time.sleep(8)
    print("mid:", pg.inner_text("#badge")[:160])
    pg.screenshot(path=f"{out}/ui_mid.png", timeout=120000)
    pg.wait_for_function("document.querySelector('#go').textContent === 'Lay out'", timeout=300000)
    print(f"done after {time.time() - t0:.1f}s: {pg.inner_text('#badge')}")
    print("stats:", pg.inner_text("#stats").replace("\n", " | "))
    pg.screenshot(path=f"{out}/ui_done.png", timeout=120000)
    print("JS errors:", errs)
    b.close()
