"""Quick scrape test — opens Chrome, visits one X account, reports what it sees."""
import asyncio
import pathlib
import os
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

from playwright.async_api import async_playwright
from datetime import datetime, timedelta

PROFILE_DIR = pathlib.Path(__file__).parent.parent / "browser_profile_chrome"

BROWSER_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]

def find_browser():
    for path in BROWSER_CANDIDATES:
        if os.path.exists(path):
            return path
    raise RuntimeError("No Chrome found.")

async def main():
    headless = "--headless" in sys.argv
    print(f"Mode: {'headless' if headless else 'visible'}")
    print(f"Profile: {PROFILE_DIR}")

    async with async_playwright() as p:
        print("Starting Chrome...")
        try:
            ctx = await p.chromium.launch_persistent_context(
                str(PROFILE_DIR),
                executable_path=find_browser(),
                headless=headless,
                args=["--disable-blink-features=AutomationControlled"],
                timeout=15000,
            )
            print("Chrome started OK")
        except Exception as e:
            print(f"FAILED to start Chrome: {e}")
            return

        page = await ctx.new_page()
        test_handle = "elonmusk"
        url = f"https://x.com/{test_handle}/with_replies"
        print(f"Navigating to {url} ...")

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(3)
            current_url = page.url
            title = await page.title()
            print(f"Landing URL : {current_url}")
            print(f"Page title  : {title}")

            if "login" in current_url or "flow" in current_url:
                print("RESULT: NOT LOGGED IN - redirected to login")
            elif test_handle.lower() in current_url.lower():
                tweets = await page.query_selector_all('article[data-testid="tweet"]')
                print(f"RESULT: LOGGED IN - found {len(tweets)} tweet articles on page")
            else:
                html = await page.content()
                print(f"RESULT: Unexpected page. First 300 chars of body:")
                print(html[:300])
        except Exception as e:
            print(f"Navigation error: {e}")
        finally:
            await page.close()
            await ctx.close()

asyncio.run(main())
