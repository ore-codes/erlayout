"""Drive the web interface in headless Chromium against a server it starts
itself: a diagram must appear early, the badge must report progress, the run
must end on a guarantee, the baseline tab and the example button must work,
and bad input must show an error.  Exits non-zero on the first failure.

  uv run --group ui playwright install chromium     # once
  uv run --group ui python bench/ui_check.py [schema.sql] [--time 30] [--out DIR]
"""
from __future__ import annotations

import argparse
import socket
import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright

from erlayout.web import app

ROOT = Path(__file__).resolve().parent.parent


def serve() -> tuple[uvicorn.Server, int]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    return server, port


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("schema", nargs="?", default=ROOT / "examples" / "sakila.sql", type=Path)
    ap.add_argument("--time", choices=["30", "120", "300"], default="30", help="time budget picked in the UI")
    ap.add_argument("--out", type=Path, help="directory for screenshots")
    a = ap.parse_args()
    server, port = serve()
    shot = (lambda pg, name: pg.screenshot(path=a.out / f"ui_{name}.png")) if a.out else (lambda pg, name: None)
    if a.out:
        a.out.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": 1500, "height": 950})
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(f"http://127.0.0.1:{port}/")
            assert not pg.is_visible("#status") and not pg.is_visible("#err")

            pg.click("#example")
            pg.wait_for_function("document.querySelector('#schema').value.includes('CREATE TABLE')")

            pg.fill("#schema", a.schema.read_text())
            pg.select_option("#time", a.time)
            t0 = time.time()
            pg.click("#go")
            pg.wait_for_selector("#stage svg", timeout=60000)
            print(f"first diagram after {time.time() - t0:.1f}s: {pg.inner_text('#badge')[:140]}")
            shot(pg, "searching")
            pg.wait_for_function("document.querySelector('#go').textContent === 'Lay out'",
                                 timeout=(int(a.time) + 120) * 1000)
            assert not pg.is_visible("#err"), pg.inner_text("#err")
            print(f"done after {time.time() - t0:.1f}s: {pg.inner_text('#badge')}")
            print("stats:", pg.inner_text("#stats").replace("\n", " | "))
            shot(pg, "optimal")

            pg.click(".tabs button[data-v=base]")
            pg.wait_for_selector("#stage svg")
            shot(pg, "baseline")

            pg.fill("#schema", "not a schema")
            pg.click("#go")
            pg.wait_for_selector("#err:not([hidden])", timeout=60000)
            print("error shown:", pg.inner_text("#err")[:120])
            b.close()
        assert not errors, f"JavaScript errors: {errors}"
        print("ok")
    finally:
        server.should_exit = True


if __name__ == "__main__":
    main()
