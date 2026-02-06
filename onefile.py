#!/usr/bin/env python3
"""
download_kb.py
--------------
Download a login-protected ServiceNow KB page using Playwright storage state.

Fix included:
- The login page has <body>, so we DON'T use wait_for_selector("body") to decide login is done.
- Instead, we keep the browser open until the user presses ENTER (and optionally verify via URL/selector).

Usage:
  pip install playwright
  playwright install

  python download_kb.py --url "YOUR_URL"
"""

import argparse
import re
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

DEFAULT_URL = "https://myservice.lloyds.com/now/nav/ui/classic/params/target/kb_view.do%3Fsysparm_article%3DKB0010611"


def safe_filename(name: str, default: str = "page") -> str:
    name = (name or "").strip() or default
    name = re.sub(r"[^\w\-\.]+", "_", name)
    return name[:180]


def login_and_save_state(
    p,
    url: str,
    state_file: Path,
    timeout_ms: int,
    login_check_url: str | None = None,
    login_check_selector: str | None = None,
) -> None:
    print("\n[1/2] Launching a visible browser for manual login...\n")

    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()

    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

    print("➡️  Please complete login in the opened browser window (SSO/MFA as needed).")
    print("➡️  After you are logged in and the KB page is accessible, return to this terminal.\n")

    # Optional automatic checks (if provided)
    if login_check_url:
        print(f"🔎 Waiting for URL to contain: {login_check_url!r}")
        try:
            page.wait_for_url(f"**{login_check_url}**", timeout=timeout_ms)
            print("✅ URL check passed.")
        except PlaywrightTimeoutError:
            print("⚠️  URL check timed out (this is not fatal).")

    if login_check_selector:
        print(f"🔎 Waiting for selector to appear: {login_check_selector!r}")
        try:
            page.wait_for_selector(login_check_selector, timeout=timeout_ms)
            print("✅ Selector check passed.")
        except PlaywrightTimeoutError:
            print("⚠️  Selector check timed out (this is not fatal).")

    # Always allow manual confirmation — most reliable for SSO/MFA
    input("✅ When you're done logging in (and can access the KB), press ENTER to save session... ")

    # Basic sanity check (won’t block, just warns)
    current_url = (page.url or "").lower()
    if any(s in current_url for s in ("login", "sso", "saml", "auth")):
        print(f"⚠️  You may still be on an auth-related URL:\n    {page.url}")
        print("    If you aren't actually logged in, re-run with --relogin and try again.\n")

    state_file.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(state_file))
    print(f"✅ Saved session storage state to: {state_file}\n")

    browser.close()


def fetch_and_save(
    p,
    url: str,
    state_file: Path,
    out_dir: Path,
    out_base: str,
    selector: str | None,
    timeout_ms: int,
    headless: bool,
    wait_until: str,
) -> None:
    print("[2/2] Fetching page with stored session...")

    browser = p.chromium.launch(headless=headless)
    context = browser.new_context(storage_state=str(state_file))
    page = context.new_page()

    try:
        page.goto(url, wait_until=wait_until, timeout=timeout_ms)
    except PlaywrightTimeoutError:
        print(f"❌ Timed out navigating to the page (wait_until={wait_until}).")
        print("   Tips:")
        print("   - Try --wait domcontentloaded (faster, less strict than networkidle)")
        print("   - Increase --timeout (ms)")
        browser.close()
        sys.exit(3)

    # Save rendered HTML
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / f"{out_base}.html"
    html_path.write_text(page.content(), encoding="utf-8")
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
            print("    HTML was still saved; inspect it to find a better selector.")

    browser.close()
    print("\nDone.")


def main():
    ap = argparse.ArgumentParser(description="Download a login-protected webpage using Playwright storage state.")
    ap.add_argument("--url", default=DEFAULT_URL, help="Target page URL")
    ap.add_argument("--state", default="storage_state.json", help="Path to storage state JSON")
    ap.add_argument("--outdir", default="downloads", help="Output directory for saved files")
    ap.add_argument("--name", default="", help="Base filename (without extension). If empty, auto-detect from KB number.")
    ap.add_argument("--selector", default="", help="CSS selector to extract text (optional)")
    ap.add_argument("--relogin", action="store_true", help="Force re-login and overwrite state")
    ap.add_argument("--timeout", type=int, default=300_000, help="Timeout in milliseconds (default 300000)")
    ap.add_argument("--headed", action="store_true", help="Use a visible browser for the fetch step too")

    ap.add_argument("--wait", choices=["domcontentloaded", "load", "networkidle"],
                    default="networkidle", help="Playwright wait_until strategy for fetch step")

    # Optional login “signals” (useful if you want auto-wait)
    ap.add_argument("--login-check-url", default="", help="During login, wait for URL to contain this substring (optional)")
    ap.add_argument("--login-check-selector", default="", help="During login, wait for selector to appear (optional)")

    args = ap.parse_args()

    url = args.url
    state_file = Path(args.state)
    out_dir = Path(args.outdir)

    # Output base name
    if args.name.strip():
        out_base = safe_filename(args.name.strip())
    else:
        m = re.search(r"(KB\d+)", url)
        out_base = safe_filename(m.group(1) if m else "page")

    selector = args.selector.strip() or None
    headless_fetch = not args.headed

    login_check_url = args.login_check_url.strip() or None
    login_check_selector = args.login_check_selector.strip() or None

    with sync_playwright() as p:
        if args.relogin or not state_file.exists():
            login_and_save_state(
                p,
                url=url,
                state_file=state_file,
                timeout_ms=args.timeout,
                login_check_url=login_check_url,
                login_check_selector=login_check_selector,
            )

        fetch_and_save(
            p,
            url=url,
            state_file=state_file,
            out_dir=out_dir,
            out_base=out_base,
            selector=selector,
            timeout_ms=args.timeout,
            headless=headless_fetch,
            wait_until=args.wait,
        )


if __name__ == "__main__":
    main()
