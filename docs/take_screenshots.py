"""Rental360 — generate README screenshots from the running app.

Requires a browser-capable environment (Playwright + Chromium) and the
backend running on http://localhost:8000:

    pip install playwright
    playwright install chromium
    uvicorn app.main:app --port 8000 &
    python docs/take_screenshots.py

Writes docs/screenshots/dashboard-top.png, dashboard-bottom.png and
assistant.png (the assistant shot asks a sample question first).
"""
from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).resolve().parent / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)
BASE = "http://localhost:8000"


def main() -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(BASE, wait_until="networkidle")
        page.wait_for_timeout(2500)  # let Chart.js render
        page.screenshot(path=OUT / "dashboard-top.png")
        page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.45)")
        page.wait_for_timeout(800)
        page.screenshot(path=OUT / "dashboard-bottom.png")
        # Assistant shot: ask a sample question and capture the answer.
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.fill("#assistant-input", "Which branch generated the most revenue?")
        page.click("#assistant-send")
        page.wait_for_timeout(2500)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.screenshot(path=OUT / "assistant.png")
        browser.close()
    print(f"Screenshots written to {OUT}")


if __name__ == "__main__":
    main()
