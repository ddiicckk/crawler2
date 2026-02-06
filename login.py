from playwright.sync_api import sync_playwright

URL = "https://myservice.lloyds.com/now/nav/ui/classic/params/target/kb_view.do%3Fsysparm_article%3DKB0010611"
STATE_FILE = "lloyds_storage_state.json"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)  # visible window for manual login
    context = browser.new_context()
    page = context.new_page()
    page.goto(URL, wait_until="domcontentloaded", timeout=60000)

    print("➡️ Please complete login in the opened browser window.")
    print("➡️ After the KB article loads, return here; waiting up to 5 minutes...")

    # Wait for something that indicates the KB page is loaded.
    # If you know a selector for the article body, replace "body" with it.
    page.wait_for_selector("body", timeout=300_000)

    context.storage_state(path=STATE_FILE)
    print(f"✅ Saved auth state to {STATE_FILE}")

    browser.close()
