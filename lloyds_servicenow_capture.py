#!/usr/bin/env python3
"""
lloyds_servicenow_capture.py

Usage examples:

# Step 1: Login (headful). This opens a browser so you can complete SSO/MFA.
python lloyds_servicenow_capture.py login \
  --url "https://myservice.lloyds.com/now/nav/ui/classic/params/target/kb_view.do?sysparm_article=KB0010611" \
  --state auth_state.json

# Step 2: Save the page using the stored session (headless by default)
# Option A: MHTML (best for full-fidelity offline archive)
python lloyds_servicenow_capture.py save \
  --url "https://myservice.lloyds.com/now/nav/ui/classic/params/target/kb_view.do?sysparm_article=KB0010611" \
  --state auth_state.json --format mhtml --out KB0010611.mhtml

# Option B: PDF (print snapshot)
python lloyds_servicenow_capture.py save \
  --url "https://myservice.lloyds.com/now/nav/ui/classic/params/target/kb_view.do?sysparm_article=KB0010611" \
  --state auth_state.json --format pdf --out KB0010611.pdf

# Option C: Rendered HTML (DOM after JS)
python lloyds_servicenow_capture.py save \
  --url "https://myservice.lloyds.com/now/nav/ui/classic/params/target/kb_view.do?sysparm_article=KB0010611" \
  --state auth_state.json --format html --out KB0010611_rendered.html
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/118.0.0.0 Safari/537.36"
)


def scroll_lazy(page, step=900, delay_ms=400):
    """Scroll to trigger lazy-loaded content."""
    page.evaluate(
        """async ({step, delay}) => {
            const sleep = ms => new Promise(r => setTimeout(r, ms));
            let prevHeight = 0;
            for (let i = 0; i < 1000; i++) {
              window.scrollBy(0, step);
              await sleep(delay);
              const sh = document.documentElement.scrollHeight;
              if (sh === prevHeight) break;
              prevHeight = sh;
            }
        }""",
        {"step": step, "delay": delay_ms},
    )
    # Small settle time
    page.wait_for_timeout(1200)


def open_and_wait(page, url, wait="networkidle", timeout_ms=120000):
    """Navigate and wait for the page to load/settle."""
    page.goto(url, wait_until=wait, timeout=timeout_ms)
    # Some SPAs/ServiceNow pages keep fetching; give it a moment
    page.wait_for_timeout(1500)


def login_and_save_state(url: str, state_path: Path, viewport=(1366, 900)):
    """
    Opens a visible browser so you can complete SSO/MFA. After the
    target page loads successfully, session cookies are saved to state_path.
    """
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(
            user_agent=DEFAULT_UA,
            viewport={"width": viewport[0], "height": viewport[1]},
        )
        page = ctx.new_page()

        print("[info] Opening login page. Complete SSO/MFA in the browser window...")
        open_and_wait(page, url)

        # Tip printed to terminal for user confirmation
        print("\n=== ACTION REQUIRED ===")
        print("1) In the opened browser, complete your login (SSO/MFA).")
        print("2) Wait until the *target KB article page* is fully visible.")
        print("3) Return here and press ENTER to continue.")
        input("Press ENTER only when the KB page content is visible... ")

        # Final settle + lazy-load trigger
        try:
            scroll_lazy(page)
        except Exception:
            pass

        # Quick check: ensure we are not on an auth page (heuristic)
        url_now = page.url
        if any(k in url_now.lower() for k in ["login", "signin", "sso", "adfs"]):
            print("[warn] You appear to still be on a login page. "
                  "If this is incorrect, continue; otherwise, log in then retry.")
        else:
            print(f"[info] Landed on: {url_now}")

        # Persist storage (cookies/localStorage)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        ctx.storage_state(path=str(state_path))
        print(f"[done] Auth/session saved to: {state_path}")

        # Keep a quick screenshot for validation (optional)
        try:
            shot_path = state_path.with_suffix(".png")
            page.screenshot(path=str(shot_path), full_page=True)
            print(f"[info] Screenshot saved to: {shot_path}")
        except Exception:
            pass

        browser.close()


def save_mhtml(ctx, url: str, out_path: Path, viewport=(1366, 900)):
    page = ctx.new_page()
    open_and_wait(page, url)
    scroll_lazy(page)
    # Use CDP to capture MHTML
    cdp = ctx.new_cdp_session(page)
    cdp.send("Page.enable")
    snapshot = cdp.send("Page.captureSnapshot", {"format": "mhtml"})
    data = snapshot.get("data", "")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(data, encoding="utf-8")
    print(f"[done] MHTML saved: {out_path}")
    page.close()


def save_pdf(ctx, url: str, out_path: Path, viewport=(1366, 900)):
    page = ctx.new_page()
    open_and_wait(page, url)
    scroll_lazy(page)
    # Use CDP printToPDF for consistent results (A4 by default)
    cdp = ctx.new_cdp_session(page)
    cdp.send("Page.enable")
    pdf_obj = cdp.send(
        "Page.printToPDF",
        {
            "printBackground": True,
            "paperWidth": 8.27,   # A4 width in inches
            "paperHeight": 11.69, # A4 height in inches
            "marginTop": 0.4,
            "marginBottom": 0.6,
            "marginLeft": 0.4,
            "marginRight": 0.4,
            "preferCSSPageSize": False,
            "scale": 1.0,
            "displayHeaderFooter": False,
        },
    )
    pdf_bytes = bytes(pdf_obj["data"], "utf-8")
    # Data is base64 per CDP; decode
    import base64
    pdf_bytes = base64.b64decode(pdf_obj["data"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(pdf_bytes)
    print(f"[done] PDF saved: {out_path}")
    page.close()


def save_rendered_html(ctx, url: str, out_path: Path, viewport=(1366, 900)):
    page = ctx.new_page()
    open_and_wait(page, url)
    scroll_lazy(page)
    html = page.content()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"[done] Rendered HTML saved: {out_path}")
    page.close()


def save_with_state(url: str, state_path: Path, out: Path, fmt: str, viewport=(1366, 900), headless=True):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless, args=["--disable-gpu", "--no-sandbox"])
        ctx = browser.new_context(
            user_agent=DEFAULT_UA,
            viewport={"width": viewport[0], "height": viewport[1]},
            storage_state=str(state_path) if state_path.exists() else None,
        )

        try:
            if fmt == "mhtml":
                save_mhtml(ctx, url, out, viewport)
            elif fmt == "pdf":
                save_pdf(ctx, url, out, viewport)
            elif fmt == "html":
                save_rendered_html(ctx, url, out, viewport)
            else:
                raise ValueError(f"Unsupported format: {fmt}")
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser(description="Capture Lloyds ServiceNow KB page with SSO.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_login = sub.add_parser("login", help="Open a visible browser; complete SSO/MFA; save session state.")
    p_login.add_argument("--url", required=True, help="Target page URL (will redirect to login as needed).")
    p_login.add_argument("--state", default="auth_state.json", help="Path to save Playwright storage state.")
    p_login.add_argument("--width", type=int, default=1366)
    p_login.add_argument("--height", type=int, default=900)

    p_save = sub.add_parser("save", help="Use saved session state to fetch and save the page.")
    p_save.add_argument("--url", required=True, help="Target KB URL.")
    p_save.add_argument("--state", default="auth_state.json", help="Path to Playwright storage state (from login step).")
    p_save.add_argument("--format", choices=["mhtml", "pdf", "html"], default="mhtml", help="Output format.")
    p_save.add_argument("--out", required=True, help="Output file path, e.g., KB0010611.mhtml")
    p_save.add_argument("--width", type=int, default=1366)
    p_save.add_argument("--height", type=int, default=900)
    p_save.add_argument("--headful", action="store_true", help="Show browser window while saving (debug).")

    args = parser.parse_args()

    if args.cmd == "login":
        login_and_save_state(args.url, Path(args.state), viewport=(args.width, args.height))
    elif args.cmd == "save":
        save_with_state(
            url=args.url,
            state_path=Path(args.state),
            out=Path(args.out),
            fmt=args.format,
            viewport=(args.width, args.height),
            headless=(not args.headful),
        )


if __name__ == "__main__":
    main()
