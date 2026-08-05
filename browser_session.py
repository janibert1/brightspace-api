"""Persistent Playwright session for a Brightspace (D2L) install, so you
don't have to fight your institution's SSO/MFA on every single request.
First run needs a real interactive login once (headed browser, see
scripts/bootstrap_login.py) - after that, storage_state.json holds the
session cookies and every subsequent run can go headless and reuse it.

This is the honest shape of "bypass repeated login prompts": there's no
way to script around a real MFA challenge from the outside, so the design
is "log in once by hand, persist the session, refresh it interactively
again whenever it finally expires." No credential-guessing, no MFA-bypass
trickery - if your institution's login has a CAPTCHA on top of MFA, this
approach still works for the human-driven bootstrap step, it just doesn't
make headless *first-time* login possible (nothing does, short of solving
the CAPTCHA yourself in the browser window that pops up).

See README.md's "Session lifetime" section for what's actually known
about how long a saved session survives before needing a fresh login.
"""
from pathlib import Path

from playwright.sync_api import sync_playwright, BrowserContext

from config import cfg

SESSION_DIR = Path(__file__).resolve().parent / ".browser_session"
STORAGE_STATE_FILE = SESSION_DIR / "storage_state.json"


def has_saved_session() -> bool:
    return STORAGE_STATE_FILE.exists()


def bootstrap_login_interactive():
    """Run this once, by hand, on a machine with a real display (or over
    VNC/X-forwarding) - NOT from cron. Opens a real headed browser so you
    can complete login (including MFA/CAPTCHA, whatever your institution
    requires) yourself, then saves the resulting session."""
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(cfg.brightspace_base_url)
        print("Log in (including MFA/CAPTCHA if prompted) in the opened browser window, "
              "then come back here and press Enter.")
        input()
        context.storage_state(path=str(STORAGE_STATE_FILE))
        browser.close()
    print(f"Session saved to {STORAGE_STATE_FILE}")


def open_context(playwright, headless: bool = True) -> BrowserContext:
    if not has_saved_session():
        raise RuntimeError(
            "No saved Brightspace session - run `python scripts/bootstrap_login.py` "
            "interactively first (needs a real display for the login/MFA step)."
        )
    browser = playwright.chromium.launch(headless=headless)
    return browser.new_context(storage_state=str(STORAGE_STATE_FILE))
