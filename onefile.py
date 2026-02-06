#!/usr/bin/env python3
"""
download_kb.py - ServiceNow KB downloader (SSO-friendly, no iframe assumptions)

- Uses Playwright persistent profile for reliable enterprise SSO.
- After login, navigates to decoded KB URL (kb_view.do?sysparm_article=KBxxxx).
- Waits until KB number appears in page text AND text length is non-trivial.
- Saves screenshot, HTML, URL, and a text preview for debugging.

Install:
  pip install playwright
  playwright install

First run (login):
  python download_kb.py --headed --relogin

Later runs:
  python download_kb.py --headed
"""

import argparse
import re
import shutil
import time
from pathlib import Path
from datetime import datetime
from urllib.parse import unquote

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


DEFAULT_URL = "https://myservice.lloyds.com/now/nav/ui/classic/params/target/kb_view.do%3Fsysparm_article%3DKB0010611"


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def safe_filename(name: str, default: str = "page") -> str:
    name = (name or "").strip() or default
    name = re.sub(r"[^\w\-\.]+", "_", name)
    return name[:180]


def looks_like_login(url: str) -> bool:
    u = (url or "").lower()
    return any(s in u for s in ("login", "sso", "saml", "auth", "okta", "adfs", "signin"))


def extract_kb_number(url: str) -> str | None:
    m = re.search(r"(KB\d+)", url)
    return m.group(1) if m else None


def decode_target_to_direct_url(url: str) -> str:
    """
    Converts:
      .../target/kb_view.do%3Fsysparm_article%3DKB0010611
    into:
      https://<host>/kb_view.do?sysparm_article=KB0010611

    If it can’t detect a target, returns the original URL.
    """
    # Try to find ".../target/<encoded>"
    m = re.search(r"/target/([^?]+)", url)
    if not m:
        return url

    encoded = m.group(1)
    decoded = unquote(encoded)  # kb_view.do?sysparm_article=KB...
    # If decoded already contains "http", keep it; otherwise prefix scheme+host
    if decoded.startswith("http://") or decoded.startswith("https://"):
        return decoded

    # Extract scheme+host from original URL
    host = re.match(r"^(https?://[^/]+)", url)
    if not host:
        return url

    return f"{host.group(1)}/{decoded.lstrip('/')}"


def attach_debug(page):
    page.on("console", lambda msg: print(f"[console] {msg.type}: {msg.text}"))
    page.on("pageerror", lambda err: print(f"[pageerror] {err}"))
    page.on("requestfailed", lambda req: print(f"[requestfailed] {req.url} -> {req.failure}"))


def wait_for_text_contains(page, needle: str, timeout_ms: int) -> bool:
    deadline = time.time() + timeout_ms / 1000
    needle = needle.strip()
    while time.time() < deadline:
        try:
            txt = page.evaluate("() => (document.body && document.body.innerText || '')")
            if needle in txt:
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def wait_for_text_length(page, min_chars: int, timeout_ms: int) -> int:
    deadline = time.time() + timeout_ms / 1000
    last_len = 0
    while time.time() < deadline:
        try:
            last_len = page.evaluate("() => ((document.body && document.body.innerText) || '').trim().length")
            if last_len >= min_chars:
                return last_len
        except Exception:
            pass
        time.sleep(0.25)
    return last_len


def main():
    ap = argparse.ArgumentParser(description="Download a login-protected ServiceNow KB page via Playwright.")
    ap.add_argument("--url", default=DEFAULT_URL, help="KB URL (nav wrapper or direct)")
    ap.add_argument("--outdir", default="downloads", help="Output directory")
    ap.add_argument("--profile-dir", default="pw_profile", help="Persistent browser profile directory")
    ap.add_argument("--relogin", action="store_true", help="Delete profile dir and login again")
    ap.add_argument("--headed", action="store_true", help="Visible browser window (recommended for SSO)")
    ap.add_argument("--timeout", type=int, default=240_000, help="Timeout ms (default 240000)")
    ap.add_argument("--min-chars", type=int, default=800, help="Minimum body text length to consider loaded")
    ap.add_argument("--name", default="", help="Output base name (optional)")
    args = ap.parse_args()

    out_dir = Path(args.outdir)
    out_dir.mkdir(parents=True, exist_ok=True)

    profile_dir = Path(args.profile_dir)
    if args.relogin and profile_dir.exists():
        print(f"🧹 Removing profile dir for relogin: {profile_dir}")
        shutil.rmtree(profile_dir, ignore_errors=True)

    kb = extract_kb_number(args.url) or "KB"
    base_name = safe_filename(args.name if args.name.strip() else kb)
    ts = stamp()
    base = f"{base_name}_{ts}"

    # Prefer direct URL after login
    direct_url = decode_target_to_direct_url(args.url)
    print(f"Direct URL (decoded if possible): {direct_url}")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=(not args.headed),
            viewport={"width": 1400, "height": 900},
        )
        page = context.new_page()
        page.set_default_timeout(args.timeout)
        attach_debug(page)

        print(f"➡️ Opening initial URL: {args.url}")
        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=args.timeout)
        except PlaywrightTimeoutError:
            print("⚠️ Initial goto timed out (domcontentloaded). Continuing...")

        # If we're on login, user must login (headed mode required)
        if looks_like_login(page.url):
            print(f"🔐 Login/SSO detected: {page.url}")
            if not args.headed:
                print("❗ You’re running headless. Re-run with --headed to complete login.")
                context.close()
                return
            input("✅ Complete login in the browser, then press ENTER here...")

        # Now navigate to direct URL (less shell, more content)
        print(f"➡️ Navigating to direct KB URL: {direct_url}")
        try:
            page.goto(direct_url, wait_until="domcontentloaded", timeout=args.timeout)
        except PlaywrightTimeoutError:
            print("⚠️ Direct goto timed out (domcontentloaded). Continuing...")

        print(f"   Current URL: {page.url}")
        if looks_like_login(page.url):
            print("❌ Redirected back to login/SSO. Session likely not valid. Try --relogin.")
            context.close()
            return

        # Wait for content: KB number presence + minimum text length
        kb_number = extract_kb_number(direct_url)
        if kb_number:
            print(f"⏳ Waiting for page text to contain KB number: {kb_number}")
            found = wait_for_text_contains(page, kb_number, timeout_ms=args.timeout)
            print(f"   KB marker found: {found}")

        print(f"⏳ Waiting for page text length >= {args.min_chars}")
        length = wait_for_text_length(page, min_chars=args.min_chars, timeout_ms=args.timeout)
        print(f"   Observed text length: {length}")

        # Save artifacts
        png_path = out_dir / f"{base}.png"
        html_path = out_dir / f"{base}.html"
        url_path = out_dir / f"{base}.url.txt"
        txt_preview_path = out_dir / f"{base}.text_preview.txt"

        # Screenshot
        try:
            page.screenshot(path=str(png_path), full_page=True)
            print(f"✅ Screenshot: {png_path}")
        except Exception as e:
            print(f"⚠️ Screenshot failed: {e}")

        # HTML
        html_path.write_text(page.content(), encoding="utf-8")
        print(f"✅ HTML: {html_path}")

        # Final URL
        url_path.write_text(page.url or "", encoding="utf-8")
        print(f"✅ Final URL: {url_path}")

        # Text preview (first 10k chars)
        try:
            body_text = page.evaluate("() => (document.body && document.body.innerText || '').trim()")
            txt_preview_path.write_text(body_text[:10000], encoding="utf-8")
            print(f"✅ Text preview: {txt_preview_path}")
        except Exception as e:
            print(f"⚠️ Text preview failed: {e}")

        # If still empty, print hint
        if length < max(200, args.min_chars // 2):
            print("\n⚠️ Still looks like little/no content rendered.")
            print("Next debugging steps:")
            print("  1) Run with --headed and watch if an extra click/consent is needed.")
            print("  2) Check the saved .text_preview.txt and .png to see what state it’s in.")
            print("  3) If page uses a shadow DOM/web components, selectors may not reflect inner text immediately.")

        context.close()
        print("\nDone.")


if __name__ == "__main__":
    main()
