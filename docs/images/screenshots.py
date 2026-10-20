#!/usr/bin/env python3
"""Screenshot the web calculator for the README (needs Playwright with Chromium).

    python3 -m http.server 8765 --directory docs &
    python3 docs/images/screenshots.py [http://localhost:8765/calculator/]
"""
import os
import sys

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8765/calculator/"

with sync_playwright() as p:
    browser = p.chromium.launch()
    for lang, button in (("en", "#lang-en"), ("zh-TW", "#lang-zh")):
        page = browser.new_page(viewport={"width": 1280, "height": 1120}, device_scale_factor=1.5,
                                color_scheme="light")
        page.goto(URL)
        page.wait_for_timeout(600)
        page.click(button)
        page.click("[data-q=group][data-v=long]")
        page.wait_for_timeout(600)
        page.screenshot(path=os.path.join(HERE, f"calculator.{lang}.png"), clip={"x": 0, "y": 0, "width": 1280, "height": 1120})
        print(f"calculator.{lang}.png")
    browser.close()
