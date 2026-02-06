#!/usr/bin/env python3
"""
download_kb.py (ServiceNow Classic + SSO robust)

Why previous output was blank:
- ServiceNow Classic often loads KB content inside an iframe (commonly 'gsft_main').
- The outer page can be "loaded" while the iframe is still rendering.
- storage_state replay can be flaky with enterprise SSO; persistent context is more reliable.

This script:
- Uses Playwright persistent context (browser profile on disk) for reliable SSO.
- Waits for iframe content to become non-empty before saving HTML/screenshot.
- Saves outer HTML, iframe HTML, screenshot, and a frame list debug file.

Install:
  pip install playwright
  playwright install

Run (first time, login manually):
  python download_kb.py --headed --relogin

Later runs:
  python download_kb.py --headed
  (or try without --headed once it’s stable)
"""

import argparse
import os
import re
import shutil
import sys
import time
from pathlib import Path
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

DEFAULT_URL = "https://myservice.lloyds.com/now/nav/ui/classic/params/target/kb_view.do%3Fsysparm_article%3DKB0010611"


def safe_filename(name: str, default: str = "page") -> str:
    name = (name or "").strip() or default
    name = re.sub(r"[^\w\-\.]+", "_", name)
    return name[:180]


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def looks_like_login(url: str) -> bool:
    u = (url or "").lower()
    return any(s in u for s in ("login", "sso", "saml", "auth", "okta", "adfs", "signin"))


def attach_debug(page):
    page.on("console", lambda msg: print(f"[console] {msg.type}: {msg.text}"))
    page.on("pageerror", lambda err: print(f"[pageerror] {err}"))
    page.on("requestfailed", lambda req: print(f"[requestfailed] {req.url} -> {req.failure}"))


def wait_for_non_empty_text(target, timeout_ms: int, min_chars: int = 200, poll_ms: int = 250) -> int:
    """
    Wait until document.body.innerText length exceeds min_chars.
    Works for Page or Frame (both have .evaluate()).
    Returns the observed length.
    """
    deadline = time.time() + timeout_ms / 1000
    last_len = 0
    while time.time() < deadline:
        try:
            last_len = target.evaluate("() => (document.body && document.body.innerText || '').trim().length")
            if last_len >= min_chars:
                return last_len
        except Exception:
            pass
        time.sleep(poll_ms / 1000)
    return last_len


def save_text_file(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text or "", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Download a login-protected ServiceNow KB page using Playwright.")
    ap.add_argument("--url", default=DEFAULT_URL, help="Target KB URL")
    ap.add_argument("--outdir", default="downloads", help="Output directory")
    ap.add_argument("--name", default="", help="Base output name (default: KBxxxx if detected)")
    ap.add_argument("--timeout", type=int, default=180_000, help="Timeout in ms (default: 180000)")
    ap.add_argument("--headed", action="store_true", help="Run with visible browser window")
    ap.add_argument("--relogin", action="store_true", help="Clear profile and login again")
    ap.add_argument("--profile-dir", default="pw_profile", help="Persistent profile directory (default: pw_profile)")
    ap.add_argument("--frame-name", default="gsft_main", help="Classic UI iframe name (default: gsft_main)")
    ap.add_argument("--min-chars", type=int, default=200, help="Minimum text length to consider 'loaded' (default: 200)")
    ap.add_argument("--save-frame-text", action="store_true", help="Also save iframe body innerText to .txt")
    ap.add_argument("--debug", action="store_true", help="Extra debug outputs (frame list, keep browser open prompt)")
    args = ap.parse_args()

    url = args.url
    out_dir = Path(args.outdir)
    profile_dir = Path(args.profile_dir)

    if args.name.strip():
        base = safe_filename(args.name.strip())
    else:
        m = re.search(r"(KB\d+)", url)
        base = safe_filename(m.group(1) if m else "page")

    if args.relogin and profile_dir.exists():
        print(f"🧹 Removing profile dir for relogin: {profile_dir}")
        shutil.rmtree(profile_dir, ignore_errors=True)

    out_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        # Persistent context = browser profile on disk (best for enterprise SSO)
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=(not args.headed),
            viewport={"width": 1400, "height": 900},
        )
        page = context.new_page()
        page.set_default_timeout(args.timeout)
        attach_debug(page)

        print(f"➡️ Navigating: {url}")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=args.timeout)
        except PlaywrightTimeoutError:
            print("⚠️ goto timed out (domcontentloaded). Continuing anyway (common on ServiceNow).")

        # If first time / session expired: user may need to login
        if looks_like_login(page.url):
            print(f"🔐 Looks like login/SSO page: {page.url}")
            print("➡️ Please complete login in the browser window.")
            if not args.headed:
                print("❗ You are running headless. Re-run with --headed to login.")
                context.close()
                sys.exit(2)

            input("✅ After login completes and you see the KB page, press ENTER here...")

        # After login, ensure we're on target KB (or at least not stuck on auth)
        print(f"   Current URL: {page.url}")
        if looks_like_login(page.url):
            print("❌ Still on login page after waiting. Try --relogin and ensure login completes.")
            context.close()
            sys.exit(3)

        # Wait for outer page to have some content (often still small)
        outer_len = wait_for_non_empty_text(page, timeout_ms=args.timeout, min_chars=max(50, args.min_chars // 4))
        print(f"   Outer page text length observed: {outer_len}")

        # Dump frame list (debug)
        frame_list_path = out_dir / f"{base}_{stamp()}__frames.txt"
        if args.debug:
            lines = []
            for fr in page.frames:
                lines.append(f"name={fr.name!r} url={fr.url}")
            save_text_file(frame_list_path, "\n".join(lines))
            print(f"🧾 Saved frame list: {frame_list_path}")

        # Try to get classic UI iframe (gsft_main)
        frame = page.frame(name=args.frame_name)

        # Sometimes iframe exists but not immediately attached; wait for the iframe element too
        if frame is None:
            try:
                page.wait_for_selector(f"iframe[name='{args.frame_name}']", timeout=30_000)
                frame = page.frame(name=args.frame_name)
            except PlaywrightTimeoutError:
                frame = None

        # Save artifacts AFTER waiting for real content
        ts = stamp()
        base_ts = f"{base}_{ts}"

        # Wait for iframe content if present; this is where KB usually is
        frame_len = None
        if frame is not None:
            print(f"🧩 Found frame '{args.frame_name}'. Waiting for KB content to render inside it...")
            try:
                # Wait for frame document to be ready-ish
                frame.wait_for_load_state("domcontentloaded", timeout=60_000)
            except Exception:
                pass

            frame_len = wait_for_non_empty_text(frame, timeout_ms=args.timeout, min_chars=args.min_chars)
            print(f"   Frame text length observed: {frame_len}")

        else:
            print(f"ℹ️ Frame '{args.frame_name}' not found. Will save outer page only.")
            print("   (Run with --debug to see frame names/URLs captured.)")

        # Take screenshot (full page)
        png_path = out_dir / f"{base_ts}.png"
        try:
            page.screenshot(path=str(png_path), full_page=True)
            print(f"✅ Screenshot saved: {png_path}")
        except Exception as e:
            print(f"⚠️ Screenshot failed: {e}")

        # Save outer HTML
        outer_html_path = out_dir / f"{base_ts}.html"
        outer_html_path.write_text(page.content(), encoding="utf-8")
        print(f"✅ Outer HTML saved: {outer_html_path}")

        # Save iframe HTML (best chance of actual KB content)
        if frame is not None:
            frame_html_path = out_dir / f"{base_ts}__frame_{safe_filename(args.frame_name)}.html"
            try:
                frame_html_path.write_text(frame.content(), encoding="utf-8")
                print(f"✅ Frame HTML saved: {frame_html_path}")
            except Exception as e:
                print(f"⚠️ Frame HTML save failed: {e}")

            if args.save_frame_text:
                frame_txt_path = out_dir / f"{base_ts}__frame_{safe_filename(args.frame_name)}.txt"
                try:
                    txt = frame.evaluate("() => (document.body && document.body.innerText || '').trim()")
                    frame_txt_path.write_text(txt, encoding="utf-8")
                    print(f"✅ Frame text saved: {frame_txt_path}")
                except Exception as e:
                    print(f"⚠️ Frame text save failed: {e}")

        # Final helpful hint if still empty
        if (frame_len is not None and frame_len < args.min_chars) and outer_len < max(50, args.min_chars // 4):
            print("\n⚠️ It still looks like content did not render (text lengths are low).")
            print("Possible causes & fixes:")
            print("  - The KB content is in a DIFFERENT frame name than 'gsft_main' -> run with --debug")
            print("  - A post-login redirect needs extra time -> increase --timeout (e.g., 300000)")
            print("  - The KB page requires additional clicks/consent -> run --headed and observe")
            print("  - Your org blocks automation in headless -> keep using --headed")

        if args.debug and args.headed:
            input("\n(debug) Press ENTER to close the browser...")

        context.close()
        print("\nDone.")


if __name__ == "__main__":
    main()
