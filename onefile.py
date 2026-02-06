#!/usr/bin/env python3
"""
download_kb.py
--------------
Download a login-protected ServiceNow KB page using Playwright.

How it works:
1) If no storage state exists (or --relogin), launches a visible browser so you can log in.
   After login, it saves cookies/localStorage to a JSON file.
2) Then (or on subsequent runs) uses that stored session to fetch the page and save:
   - rendered HTML (page.content())
   - optional extracted text from a CSS selector

Notes:
- This script requires you to have legitimate access.
- Storage state may expire; rerun with --relogin when needed.
"""

import argparse
import os
import re
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


DEFAULT_URL = "https://myservice.lloyds.com/now/nav/ui/classic/params/target/kb_view.do%3Fsysparm_article%3DKB0010611"


def safe_filename(name: str, default: str = "page") -> str:
    name = name.strip() or default
    name = re.sub(r"[^\w\-\.]+", "_", name)
    return name[:180]


def login_and_save_state(p, url: str, state_file: Path, timeout_ms: int) -> None:
    print("\n[1/2] No valid session state found (or relogin requested).")
    print("      Launching a visible browser for manual login...\n")

    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()

    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

    print("➡️  Please complete login in the opened browser window (SSO/MFA as needed).")
    print("➡️  After the KB page loads, come back here. I'll wait up to {:.0f} seconds.\n"
          .format(timeout_ms / 1000))

    # Basic wait: page body loads (doesn't guarantee article is visible, but is safe)
    try:
        page.wait_for_selector("body", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        print("❌ Timed out waiting for the page to load after login.")
        print("   Tip: If login needs more time, rerun with a larger --timeout (ms).")
        browser.close()
        sys.exit(2)

    state_file.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(state_file))
    print(f"✅ Saved session storage state to: {state_file}\n")

    browser.close()


def fetch_and_save(p, url: str, state_file: Path, out_dir: Path, out_base: str,
                   selector: str | None, timeout_ms: int, headless: bool) -> None:
    print("[2/2] Fetching page with stored session...")

    browser = p.chromium.launch(headless=headless)
    context = browser.new_context(storage_state=str(state_file))
    page = context.new_page()

    try:
        page.goto(url, wait_until="networkidle", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        print("❌ Timed out navigating to the page (networkidle).")
        print("   Tip: Try a larger --timeout or use --wait domcontentloaded.")
        browser.close()
        sys.exit(3)

    # Save rendered HTML
    html = page.content()
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / f"{out_base}.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"✅ Saved rendered HTML: {html_path}")

    # Optionally save extracted text
    if selector:
        try:
            loc = page.locator(selector).first
            loc.wait_for(state="visible", timeout=timeout_ms)
            text = loc.inner_text(timeout=timeout_ms)
            txt_path = out_dir / f"{out_base}.txt"
            txt_path.write_text(text, encoding="utf-8")
            print(f"✅ Saved extracted text ({selector}): {txt_path}")
        except PlaywrightTimeoutError:
            print(f"⚠️  Selector not found/visible within timeout: {selector}")
            print("    HTML was still saved; you can inspect it to find a better selector.")

    browser.close()


def main():
    ap = argparse.ArgumentParser(
        description="Download a login-protected webpage using Playwright storage state."
    )
    ap.add_argument("--url", default=DEFAULT_URL, help="Target page URL")
    ap.add_argument("--state", default="storage_state.json", help="Path to storage state JSON")
    ap.add_argument("--outdir", default="downloads", help="Output directory for saved files")
    ap.add_argument("--name", default="", help="Base filename (without extension). If empty, auto.")
    ap.add_argument("--selector", default="", help="CSS selector to extract text (optional)")
    ap.add_argument("--relogin", action="store_true", help="Force re-login and overwrite state")
    ap.add_argument("--timeout", type=int, default=300_000, help="Timeout in milliseconds (default 300000)")
    ap.add_argument("--headed", action="store_true", help="Use a visible browser for the fetch step too")
    args = ap.parse_args()

    url = args.url
    state_file = Path(args.state)
    out_dir = Path(args.outdir)

    # Choose output base name
    if args.name.strip():
        out_base = safe_filename(args.name.strip())
    else:
        # Try to infer KB id from URL, else "page"
        m = re.search(r"(KB\d+)", url)
        out_base = m.group(1) if m else "page"
        out_base = safe_filename(out_base)

    selector = args.selector.strip() or None
    timeout_ms = args.timeout
    headless_fetch = not args.headed

    with sync_playwright() as p:
        if args.relogin or not state_file.exists():
            login_and_save_state(p, url, state_file, timeout_ms)

        fetch_and_save(p, url, state_file, out_dir, out_base, selector, timeout_ms, headless_fetch)

    print("\nDone.")


if __name__ == "__main__":
    main()
