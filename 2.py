#!/usr/bin/env python3
import os, sys, time, argparse
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError

DEFAULT_URL = "https://myservice.lloyds.com/now/nav/ui/classic/params/target/kb_view.do?sysparm_article=KB0010611"

def is_ms_login_url(url: str) -> bool:
    url = url.lower()
    return any(host in url for host in [
        "login.microsoftonline.com",
        "login.microsoft.com",
        "sts.windows.net",
        "adfs",
        "microsoftonline"
    ])

def maybe_click(page, role=None, name=None, selector=None, timeout=3000):
    try:
        if selector:
            page.locator(selector).first.click(timeout=timeout)
        elif role and name:
            page.get_by_role(role, name=name).first.click(timeout=timeout)
        return True
    except Exception:
        return False

def maybe_fill(page, selector, value, timeout=5000):
    try:
        page.locator(selector).fill(value, timeout=timeout)
        return True
    except Exception:
        return False

def wait_any_selector(page, selectors, timeout=10000):
    for sel in selectors:
        try:
            page.wait_for_selector(sel, timeout=timeout)
            return sel
        except TimeoutError:
            continue
    return None

def try_microsoft_login(page, email, password) -> str:
    """
    Attempts to sign in on common Microsoft/Azure AD pages.
    Returns a string status: 'ok', 'mfa', or 'unknown'.
    """
    # Pick account page
    if maybe_click(page, role="button", name="Use another account"):
        pass

    # Email entry
    email_box = wait_any_selector(page, ["input[type='email']", "input[name='loginfmt']"], timeout=8000)
    if email_box:
        page.locator(email_box).fill(email)
        # Next/Submit
        if not maybe_click(page, role="button", name="Next"):
            maybe_click(page, selector="input[type='submit']")
        page.wait_for_timeout(800)

    # Password entry
    pwd_box = wait_any_selector(page, ["input[type='password']"], timeout=8000)
    if pwd_box:
        page.locator(pwd_box).fill(password)
        if not maybe_click(page, role="button", name="Sign in"):
            maybe_click(page, selector="input[type='submit']")
        page.wait_for_timeout(1000)

    # "Stay signed in?" prompt
    if wait_any_selector(page, ["#KmsiCheckbox", "input[name='DontShowAgain']"], timeout=3000):
        # tick "Don't show again" if available
        maybe_click(page, selector="#KmsiCheckbox")
        # click 'Yes' to stay signed in
        if not maybe_click(page, role="button", name="Yes"):
            maybe_click(page, role="button", name="Continue")
        page.wait_for_timeout(800)

    # Detect MFA challenges (heuristics)
    if wait_any_selector(page, [
        "text=Approve sign in request",
        "text=Enter code",
        "text=Use your Microsoft Authenticator app",
        "text=We sent a notification",
        "text=Having trouble signing in?"
    ], timeout=2000):
        return "mfa"

    return "ok"

def scroll_lazy(page):
    page.evaluate("""
        (async () => {
          const sleep = ms => new Promise(r => setTimeout(r, ms));
          let prev = 0;
          for (let i=0;istore_true {shot}")
        except Exception:
            pass

        browser.close()

if __name__ == "__main__":
    main()
