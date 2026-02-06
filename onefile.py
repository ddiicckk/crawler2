#!/usr/bin/env python3
"""
download_kb.py - ServiceNow KB downloader (SSO safe)

Fixes:
- Handles SSO redirects that interrupt page.goto (e.g., to login.microsoftonline.com)
- Retries navigation after SSO completes
- Avoids re-navigating if KB is already loaded
- Uses persistent browser profile for reliable enterprise SSO
"""

import argparse
import re
import shutil
import time
from pathlib import Path
from datetime import datetime
from urllib.parse import unquote

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from playwright._impl._errors import Error as PlaywrightError


DEFAULT_URL = "https://myservice.lloyds.com/now/nav/ui/classic/params/target/kb_view.do%3Fsysparm_article%3DKB0010611"


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def safe_filename(name: str, default: str = "page") -> str:
    name = (name or "").strip() or default
    name = re.sub(r"[^\w\-\.]+", "_", name)
    return name[:180]


def looks_like_login(url: str) -> bool:
    u = (url or "").lower()
    return any(s in u for s in ("login", "sso", "saml", "auth", "okta", "adfs", "signin", "microsoftonline.com"))


def extract_kb_number(text: str) -> str | None:
    m = re.search(r"(KB\d+)", text or "")
    return m.group(1) if m else None


def decode_target_to_direct_url(url: str) -> str:
    """
    Converts:
      .../target/kb_view.do%3Fsysparm_article%3DKB0010611
    into:
      https://<host>/kb_view.do?sysparm_article=KB0010611
    """
    m = re.search(r"/target/([^?]+)", url)
    if not m:
        return url
    encoded = m.group(1)
    decoded = unquote(encoded)  # kb_view.do?sysparm_article=KB...
    if decoded.startswith("http://") or decoded.startswith("https://"):
        return decoded
    host = re.match(r"^(https?://[^/]+)", url)
    if not host:
        return url
    return f"{host.group(1)}/{decoded.lstrip('/')}"


def attach_debug(page):
    page.on("console", lambda msg: print(f"[console] {msg.type}: {msg.text}"))
    page.on("pageerror", lambda err: print(f"[pageerror] {err}"))
    page.on("requestfailed", lambda req: print(f"[requestfailed] {req.url} -> {req.failure}"))


def get_body_text(page) -> str:
    try:
        return page.evaluate("() => (document.body && document.body.innerText || '').trim()")
    except Exception:
        return ""


def wait_for_body_text_contains(page, needle: str, timeout_ms: int) -> bool:
    deadline = time.time() + timeout_ms / 1000
    needle = (needle or "").strip()
    while time.time() < deadline:
        txt = get_body_text(page)
        if needle and needle in txt:
            return True
        time.sleep(0.25)
    return False


def wait_for_text_length(page, min_chars: int, timeout_ms: int) -> int:
    deadline = time.time() + timeout_ms / 1000
    last_len = 0
    while time.time() < deadline:
        txt = get_body_text(page)
        last_len = len(txt)
        if last_len >= min_chars:
            return last_len
        time.sleep(0.25)
    return last_len


def goto_with_sso_retry(page, url: str, timeout_ms: int, headed: bool, max_attempts: int = 3) -> None:
    """
    Navigate to a URL and tolerate SSO interruptions.
    If Playwright reports navigation interrupted by another navigation (SSO redirect),
    wait for user to complete SSO (headed) then retry.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            return
        except PlaywrightTimeoutError:
            # Even if domcontentloaded times out, content may still be there
            print(f"⚠️ goto timeout (attempt {attempt}/{max_attempts}). Continuing...")
            return
        except PlaywrightError as e:
            msg = str(e)
            if "is interrupted by another navigation" in msg:
                print(f"⚠️ Navigation interrupted by another navigation (SSO) (attempt {attempt}/{max_attempts}).")
                print(f"   Current URL now: {page.url}")

                # Give the automatic redirect a moment to complete
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=30_000)
                except Exception:
                    pass

                if looks_like_login(page.url):
                    print("🔐 Detected SSO/login page during navigation.")
                    if not headed:
                        raise RuntimeError(
                            "SSO requires interaction but you are running headless. "
                            "Re-run with --headed."
                        )
                    input("✅ Complete the SSO step in the browser window, then press ENTER to retry navigation...")

                # Retry after SSO settles
                continue

            # Any other error should be raised
            raise


def save_artifacts(page, out_dir: Path, base: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    png = out_dir / f"{base}.png"
    html = out_dir / f"{base}.html"
    final_url = out_dir / f"{base}.url.txt"
    preview = out_dir / f"{base}.text_preview.txt"

    try:
        page.screenshot(path=str(png), full_page=True)
        print(f"✅ Screenshot: {png}")
    except Exception as e:
        print(f"⚠️ Screenshot failed: {e}")

    try:
        html.write_text(page.content(), encoding="utf-8")
        print(f"✅ HTML: {html}")
    except Exception as e:
        print(f"⚠️ HTML save failed: {e}")

    try:
        final_url.write_text(page.url or "", encoding="utf-8")
        print(f"✅ Final URL: {final_url}")
    except Exception as e:
        print(f"⚠️ URL save failed: {e}")

    try:
        txt = get_body_text(page)
        preview.write_text(txt[:12000], encoding="utf-8")
        print(f"✅ Text preview: {preview}")
    except Exception as e:
        print(f"⚠️ Text preview failed: {e}")


def main():
    ap = argparse.ArgumentParser(description="Download ServiceNow KB page with SSO-friendly Playwright automation.")
    ap.add_argument("--url", default=DEFAULT_URL, help="KB URL (nav wrapper or direct)")
    ap.add_argument("--outdir", default="downloads", help="Output directory")
    ap.add_argument("--profile-dir", default="pw_profile", help="Persistent browser profile directory")
    ap.add_argument("--relogin", action="store_true", help="Delete profile dir and login again")
    ap.add_argument("--headed", action="store_true", help="Visible browser window (recommended for SSO)")
    ap.add_argument("--timeout", type=int, default=240_000, help="Timeout ms (default 240000)")
    ap.add_argument("--min-chars", type=int, default=800, help="Minimum body text length to consider loaded")
    ap.add_argument("--name", default="", help="Output base name (optional)")
    ap.add_argument("--skip-direct", action="store_true", help="Do NOT navigate to decoded direct URL after login")
    args = ap.parse_args()

    out_dir = Path(args.outdir)
    out_dir.mkdir(parents=True, exist_ok=True)

    profile_dir = Path(args.profile_dir)
    if args.relogin and profile_dir.exists():
        print(f"🧹 Removing profile dir for relogin: {profile_dir}")
        shutil.rmtree(profile_dir, ignore_errors=True)

    kb = extract_kb_number(args.url) or "KB"
    base_name = safe_filename(args.name if args.name.strip() else kb)
    base = f"{base_name}_{stamp()}"

    direct_url = decode_target_to_direct_url(args.url)
    kb_number = extract_kb_number(direct_url) or extract_kb_number(args.url)

    print(f"Target URL:  {args.url}")
    print(f"Direct URL:  {direct_url}")
    print(f"KB number:   {kb_number}")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=(not args.headed),
            viewport={"width": 1400, "height": 900},
        )
        page = context.new_page()
        page.set_default_timeout(args.timeout)
        attach_debug(page)

        # 1) Open initial URL (may land on SSO)
        print("\n➡️ Opening initial URL...")
        goto_with_sso_retry(page, args.url, timeout_ms=args.timeout, headed=args.headed)

        # If login page, user must complete it (headed required)
        if looks_like_login(page.url):
            print(f"🔐 Login/SSO detected: {page.url}")
            if not args.headed:
                context.close()
                raise RuntimeError("You are headless but SSO requires interaction. Re-run with --headed.")
            input("✅ Complete login in the browser window, then press ENTER here...")

        # 2) If KB already present, don’t re-navigate
        if kb_number:
            print(f"\n⏳ Checking if KB marker ({kb_number}) is already present...")
            if wait_for_body_text_contains(page, kb_number, timeout_ms=20_000):
                print("✅ KB marker already present; skipping direct navigation.")
            else:
                # 3) Navigate to direct URL (but tolerate SSO interruptions)
                if not args.skip_direct:
                    print("\n➡️ Navigating to direct KB URL (SSO-safe)...")
                    goto_with_sso_retry(page, direct_url, timeout_ms=args.timeout, headed=args.headed)
                else:
                    print("\nℹ️ --skip-direct set; not navigating to direct URL.")
        else:
            # If we can’t detect KB number, still attempt direct URL unless skipped
            if not args.skip_direct and direct_url != args.url:
                print("\n➡️ Navigating to direct URL (KB not detected in URL)...")
                goto_with_sso_retry(page, direct_url, timeout_ms=args.timeout, headed=args.headed)

        print(f"\n   Current URL: {page.url}")
        if looks_like_login(page.url):
            print("⚠️ Still on SSO/login page. Session may not be established for the ServiceNow app.")
            print("   Try --relogin and make sure you fully land on the KB page before pressing ENTER.")

        # 4) Wait for content to appear
        if kb_number:
            print(f"⏳ Waiting for KB marker to appear in text: {kb_number}")
            wait_for_body_text_contains(page, kb_number, timeout_ms=args.timeout)

        print(f"⏳ Waiting for body text length >= {args.min_chars}")
        length = wait_for_text_length(page, min_chars=args.min_chars, timeout_ms=args.timeout)
        print(f"   Observed text length: {length}")

        # 5) Save artifacts
        print("\n💾 Saving artifacts...")
        save_artifacts(page, out_dir, base)

        if length < max(200, args.min_chars // 2):
            print("\n⚠️ Content still looks thin. Next step is to wait on a specific network response (XHR) that returns the KB JSON/HTML.")
            print("   If you share the first 30 lines of the saved .text_preview.txt and the final URL, I’ll tailor that approach.")

        context.close()
        print("\nDone.")


if __name__ == "__main__":
    main()
