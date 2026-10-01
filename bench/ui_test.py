import sys
from playwright.sync_api import sync_playwright
ddl = open(sys.argv[1]).read()
with sync_playwright() as p:
    b = p.chromium.launch(args=["--disable-gpu"])
    pg = b.new_page(viewport={"width": 1500, "height": 950})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto("http://127.0.0.1:8765/")
    assert not pg.is_visible("#status") and not pg.is_visible("#err")
    pg.fill("#schema", ddl)
    pg.select_option("#time", "30")
    pg.click("#go")
    pg.wait_for_selector("#status:not([hidden])", timeout=300000)
    assert not pg.is_visible("#err")
    badge = pg.inner_text("#badge"); stats = pg.inner_text("#stats")
    pg.screenshot(path="bench/out/ui_optimal.png", timeout=120000)
    pg.click(".tabs button[data-v=base]")
    pg.wait_for_timeout(500)
    pg.screenshot(path="bench/out/ui_baseline.png", timeout=120000)
    # example button + error path
    pg.click("#example"); pg.wait_for_timeout(500)
    assert "CREATE TABLE" in pg.input_value("#schema")
    pg.fill("#schema", "not a schema"); pg.click("#go")
    pg.wait_for_selector("#err:not([hidden])", timeout=60000)
    err = pg.inner_text("#err")
    print("BADGE:", badge); print("STATS:", stats.replace("\n", " | ")); print("ERR:", err); print("JS errors:", errors)
    b.close()
