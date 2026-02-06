from playwright.sync_api import sync_playwright

URL = "https://myservice.lloyds.com/now/nav/ui/classic/params/target/kb_view.do%3Fsysparm_article%3DKB0010611"
STATE_FILE = "lloyds_storage_state.json"
OUT_FILE = "KB0010611_fullpage.html"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(storage_state=STATE_FILE)
    page = context.new_page()

    page.goto(URL, wait_until="networkidle", timeout=60000)

    html = page.content()
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"✅ Saved rendered HTML to {OUT_FILE}")
    browser.close()
