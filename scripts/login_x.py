"""Open a headful Chromium window so the user can log in to X.com once.

The session is stored in browser_profile/ (gitignored).
Run this script once before starting the agent daemon.
"""
import pathlib
from playwright.sync_api import sync_playwright

PROFILE_DIR = pathlib.Path(__file__).parent.parent / "browser_profile"


def main() -> None:
    PROFILE_DIR.mkdir(exist_ok=True)
    print(f"Browser profile: {PROFILE_DIR}")
    print("Log in to X.com in the browser that opens, then close it.")

    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    import os
    exe = next((p for p in candidates if os.path.exists(p)), None)
    if not exe:
        raise RuntimeError("Install Microsoft Edge or Google Chrome and retry.")
    print(f"Using browser: {exe}")

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            executable_path=exe,
            headless=False,
            args=["--start-maximized"],
        )
        page = browser.new_page()
        page.goto("https://x.com/login")
        print("Waiting for you to log in and close the browser…")
        browser.wait_for_event("close", timeout=0)

    print("Login session saved. You can now run the agent.")


if __name__ == "__main__":
    main()
