#!/usr/bin/env python3
"""
download_kb.py
--------------
Download a login-protected ServiceNow KB page using Playwright.

Key fixes for ServiceNow:
- Avoid wait_until="networkidle" by default (ServiceNow often never becomes network-idle).
- If navigation times out, still save screenshot + HTML so you get something every run.
- Optional: also save HTML from the classic UI iframe (default name: gsft_main).

Workflow:
1) First run (or --relogin): opens a visible browser for manual SSO/MFA login, then saves storage_state.json
2) Second step: uses saved session to load the KB page and save outputs.

Requires:
  pip install playwright
  playwright install
"""

import argparse
import re
import sys
from pathlib import Path
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


DEFAULT_URL = "https://myservice.lloyds.com/now/nav/ui/classic/params/target/kb_view.do%3Fsysparm_article%3DKB0010611"


def safe_filename(name: str, default: str = "page") -> str:
    name = (name or "").strip() or default
    name = re.sub(r"[^\w\-\.]+", "_", name)
    return name[:180]


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def looks_like_login(url: str) -> bool:
    u = (url or "").lower()
    return any(s in u for s in ("login", "sso", "saml", "auth", "okta", "adfs", "signin"))


def login_and_save_state(p, url: str, state_file: Path, timeout_ms: int,
                         login_check_url: str | None = None,
                         login_check_selector: str | None = None) -> None:
    print("\n[1/2] Launching a visible browser for manual login...\n")

    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.set_default_timeout(timeout_ms)

    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

    print("➡️  Complete login in the opened browser window (SSO/MFA as needed).")
    print("➡️  When you can see the KB content (or you’re fully logged in), return here.\n")

    # Optional auto-checks (non-fatal if they time out)
    if login_check_url:
        print(f"🔎 (optional) Waiting for URL to contain: {login_check_url!r}")
        try:
            page.wait_for_url(f"**{login_check_url}**", timeout=timeout_ms)
            print("✅ URL check passed.")
        except PlaywrightTimeoutError:
            print("⚠️  URL check timed out (continuing).")

    if login_check_selector:
        print(f"🔎 (optional) Waiting for selector: {login_check_selector!r}")
        try:
            page.wait_for_selector(login_check_selector, timeout=timeout_ms)
            print("✅ Selector check passed.")
        except PlaywrightTimeoutError:
            print("⚠️  Selector check timed out (continuing).")

    input("✅ Press ENTER to save the logged-in session (storage state) and continue... ")

    if looks_like_login(page.url):
        print(f"⚠️  Current URL still looks like a login page:\n    {page.url}")
        print("    If you are not actually logged in, re-run with --relogin and try again.\n")
    else:
        print(f"✅ Login step URL:\n    {page.url}\n")

    state_file.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(state_file))
    print(f"✅ Saved session storage state to: {state_file}\n")

    browser.close()


def attach_debug_listeners(page):
    # Useful if something silently fails
    page.on("console", lambda msg: print(f"[console] {msg.type}: {msg.text}"))
    page.on("pageerror", lambda err: print(f"[pageerror] {err}"))
    page.on("requestfailed", lambda req: print(f"[requestfailed] {req.url} -> {req.failure}"))


def save_artifacts(page, out_dir: Path, base: str, save_screenshot: bool = True) -> tuple[Path | None, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)

    png_path = None
    if save_screenshot:
        png_path = out_dir / f"{base}.png"
        try:
            page.screenshot(path=str(png_path), full_page=True)
            print(f"✅ Saved screenshot: {png_path}")
        except Exception as e:
            print(f"⚠️  Could not save screenshot: {e}")
            png_path = None

    html_path = out_dir / f"{base}.html"
    html_path.write_text(page.content(), encoding="utf-8")
    print(f"✅ Saved rendered HTML: {html_path}")

    return png_path, html_path


def save_frame_html(page, out_dir: Path, base: str, frame_name: str) -> Path | None:
    """
    ServiceNow classic UI often loads content inside an iframe named 'gsft_main'.
    This will save that frame's HTML if available.
    """
    frame = page.frame(name=frame_name)
    if not frame:
        # Sometimes it might be identified by URL, but we keep it simple here.
        print(f"ℹ️  Frame '{frame_name}' not found; skipping frame HTML save.")
        return None

    frame_path = out_dir / f"{base}__frame_{safe_filename(frame_name)}.html"
    try:
        frame_path.write_text(frame.content(), encoding="utf-8")
        print(f"✅ Saved frame HTML ({frame_name}): {frame_path}")
        return frame_path
    except Exception as e:
        print(f"⚠️  Could not save frame HTML ({frame_name}): {e}")
        return None


def fetch_and_save(p, url: str, state_file: Path, out_dir: Path, out_base: str,
                   selector: str | None, timeout_ms: int, headless: bool,
                   wait_until: str, frame_name: str | None,
                   save_frame: bool, wait_after_ms: int) -> None:
    print("[2/2] Fetching page with stored session...")

    browser = p.chromium.launch(headless=headless)
    context = browser.new_context(storage_state=str(state_file))
    page = context.new_page()
    page.set_default_timeout(timeout_ms)
    attach_debug_listeners(page)

    # Navigation: Do NOT rely on networkidle for ServiceNow
    try:
        page.goto(url, wait_until=wait_until, timeout=timeout_ms)
        print(f"   - Navigation completed (wait_until={wait_until}).")
    except PlaywrightTimeoutError:
        # Important: even if this times out, page may be fully visible/usable
        print(f"⚠️  Navigation timed out (wait_until={wait_until}).")
        print("   - ServiceNow often keeps background requests open, so this is common.")
        print("   - Continuing to save content anyway...")

    print(f"   - Current URL: {page.url}")

    # If we ended up back on login, warn and still save artifacts
    if looks_like_login(page.url):
        print("⚠️  It looks like you were redirected to a login/SSO page during fetch.")
        print("    Your saved session may have expired. Re-run with --relogin.\n")

    # Give the page a moment to render frames/content
    if wait_after_ms > 0:
        try:
            page.wait_for_timeout(wait_after_ms)
        except Exception:
            pass

    # Always save screenshot + HTML
    stamp = now_stamp()
    base = f"{out_base}_{stamp}"
    save_artifacts(page, out_dir, base, save_screenshot=True)

    # Optionally save iframe HTML (gsft_main is common)
    if save_frame and frame_name:
        save_frame_html(page, out_dir, base, frame_name=frame_name)

    # Optional text extraction
    if selector:
        try:
            # Try main document first
            loc = page.locator(selector).first
            loc.wait_for(state="visible", timeout=timeout_ms)
            text = loc.inner_text(timeout=timeout_ms)
            txt_path = out_dir / f"{base}.txt"
            txt_path.write_text(text, encoding="utf-8")
            print(f"✅ Saved extracted text (main doc, selector={selector}): {txt_path}")
        except PlaywrightTimeoutError:
            print(f"⚠️  Selector not found/visible in main doc within timeout: {selector}")

            # If requested, try inside the frame too
            if frame_name:
                frame = page.frame(name=frame_name)
                if frame:
                    try:
                        f_loc = frame.locator(selector).first
                        f_loc.wait_for(state="visible", timeout=timeout_ms)
                        f_text = f_loc.inner_text(timeout=timeout_ms)
                        f_txt_path = out_dir / f"{base}__frame_{safe_filename(frame_name)}.txt"
                        f_txt_path.write_text(f_text, encoding="utf-8")
                        print(f"✅ Saved extracted text (frame={frame_name}, selector={selector}): {f_txt_path}")
                    except PlaywrightTimeoutError:
                        print(f"⚠️  Selector also not found/visible in frame '{frame_name}': {selector}")

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
    ap.add_argument("--timeout", type=int, default=120_000, help="Timeout in ms (default 120000)")
    ap.add_argument("--headed", action="store_true", help="Use a visible browser for the fetch step too")

    # Important: default is domcontentloaded (better for ServiceNow than networkidle)
    ap.add_argument("--wait", choices=["domcontentloaded", "load", "networkidle"],
                    default="domcontentloaded",
                    help="Playwright wait_until strategy for fetch step (default domcontentloaded)")

    # Optional login signals
    ap.add_argument("--login-check-url", default="", help="During login, wait for URL to contain this substring (optional)")
    ap.add_argument("--login-check-selector", default="", help="During login, wait for selector to appear (optional)")

    # ServiceNow classic UI often uses iframe gsft_main
    ap.add_argument("--frame-name", default="gsft_main",
                    help="Frame name to save/extract from (default gsft_main). Use empty to disable.")
    ap.add_argument("--save-frame-html", action="store_true",
                    help="Also save the HTML of the specified frame (useful for ServiceNow classic UI).")

    ap.add_argument("--wait-after", type=int, default=3000,
                    help="Extra wait after navigation (ms) to allow UI/iframes to render (default 3000).")

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

    frame_name = (args.frame_name or "").strip() or None

    with sync_playwright() as p:
        if args.relogin or not state_file.exists():
            login_and_save_state(
                p,
                url=url,
                state_file=state_file,
                timeout_ms=args.timeout,
                login_check_url=login_check_url,
                login_check_selector=login_check_selector
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
            frame_name=frame_name,
            save_frame=args.save_frame_html,
            wait_after_ms=args.wait_after
        )


if __name__ == "__main__":
    main()
