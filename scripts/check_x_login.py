"""Opens Chrome with the agent profile. If not logged in, waits for manual login before closing."""
import asyncio
import pathlib
import os

from playwright.async_api import async_playwright

PROFILE_DIR = pathlib.Path(__file__).parent.parent / "browser_profile_chrome"

BROWSER_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

def find_browser():
    for path in BROWSER_CANDIDATES:
        if os.path.exists(path):
            return path
    raise RuntimeError("No Chrome or Edge found.")

def is_logged_in(url: str, title: str) -> bool:
    return "home" in url.lower() or "/home" in url

async def main():
    async with async_playwright() as p:
        print("Opening Chrome with agent profile...")
        ctx = await p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            executable_path=find_browser(),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = await ctx.new_page()
        await page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(3)

        url = page.url
        title = await page.title()

        if is_logged_in(url, title):
            print(f"[OK] Already logged in! URL: {url}")
        else:
            print(f"Not logged in (URL: {url})")
            print("Navigating to login page — please log in now...")
            await page.goto("https://x.com/i/flow/login", wait_until="domcontentloaded")

            print("Waiting for you to log in... (watching for x.com/home)")
            for _ in range(120):  # wait up to 4 minutes
                await asyncio.sleep(2)
                current_url = page.url
                current_title = await page.title()
                if is_logged_in(current_url, current_title):
                    print(f"\n[OK] Login detected! URL: {current_url}")
                    break
                print(f"  Still waiting... ({current_url})", end="\r")
            else:
                print("\n✗ Timed out waiting for login.")
                await ctx.close()
                return

        await asyncio.sleep(2)
        print("Saving session and closing browser...")
        await ctx.close()
        print("Done — session saved to browser_profile_chrome/")

asyncio.run(main())
