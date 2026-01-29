# save_rendered.py
# Usage: python save_rendered.py "https://example.com/page" out_dir
import sys, os, time, json, pathlib
from urllib.parse import urljoin, urlparse
from playwright.sync_api import sync_playwright
import requests

def ensure_dir(p: pathlib.Path):
    p.mkdir(parents=True, exist_ok=True)

def main(url, out_dir):
    out_dir = pathlib.Path(out_dir)
    ensure_dir(out_dir)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/118.0.0.0 Safari/537.36'),
            viewport={'width': 1366, 'height': 900},
        )
        page = ctx.new_page()
        page.goto(url, wait_until='networkidle')

        # Scroll to trigger lazy-load
        page.evaluate("""
            (async () => {
              let total = 0;
              while (total < document.documentElement.scrollHeight) {
                window.scrollBy(0, 800);
                total += 800;
                await new Promise(r => setTimeout(r, 400));
              }
            })()
        """)
        page.wait_for_timeout(1500)

        # Save rendered HTML
        html = page.content()
        html_path = out_dir / "page_rendered.html"
        html_path.write_text(html, encoding="utf-8")

        # Collect asset URLs from DOM
        asset_urls = set()
        for selector, attr in [("img", "src"), ("script", "src"), ("link[rel='stylesheet']", "href")]:
            elements = page.locator(selector)
            count = elements.count()
            for i in range(count):
                value = elements.nth(i).get_attribute(attr)
                if value and not value.startswith("data:") and not value.startswith("#"):
                    asset_urls.add(urljoin(url, value))

        # Reuse cookies to fetch assets (helps with gated CDNs)
        cookies = ctx.cookies()
        cookie_header = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
        session = requests.Session()
        session.headers.update({
            "User-Agent": ctx._options.get("user_agent", "Mozilla/5.0"),
            "Cookie": cookie_header,
            "Referer": url
        })

        assets_dir = out_dir / "assets"
        ensure_dir(assets_dir)
        downloaded = 0
        for aurl in asset_urls:
            try:
                r = session.get(aurl, timeout=30)
                r.raise_for_status()
                parsed = urlparse(aurl)
                name = parsed.path.strip("/").replace("/", "_")
                if not name:
                    name = "index"
                ext = os.path.splitext(parsed.path)[1] or ""
                local = assets_dir / f"{name}{ext}"
                local.write_bytes(r.content)
                downloaded += 1
            except Exception as e:
                print(f"[warn] asset failed: {aurl} ({e})")

        print(f"[done] HTML: {html_path}")
        print(f"[done] Assets: {downloaded} saved in {assets_dir}")
        browser.close()

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python save_rendered.py <URL> <out_dir>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
