import pathlib
import sys

from playwright.sync_api import sync_playwright

src, dst = sys.argv[1], sys.argv[2]
svg = pathlib.Path(src).read_text()
with sync_playwright() as p:
    b = p.chromium.launch(args=["--disable-gpu"])
    pg = b.new_page(viewport={"width": 1700, "height": 1100})
    pg.set_content(f"<html><body style='margin:0'>{svg}</body></html>")
    pg.screenshot(path=dst, full_page=True, timeout=180000, animations="disabled")
    b.close()
