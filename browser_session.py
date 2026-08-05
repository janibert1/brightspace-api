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

scripts/scripted_login.py is a second, fully-automated option for
institutions where the *login itself* doesn't require MFA (confirmed
live against TU Delft: Brightspace's own login goes straight through
with no challenge at all - only this institution's Microsoft 365/Outlook
login enforces MFA, not Brightspace/D2L). If your institution's
Brightspace login does require MFA, use bootstrap_login.py instead -
scripted_login will detect that it didn't reach Brightspace and fail
loudly rather than guess.
"""
import re
import subprocess
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


LOGIN_FORM_URL_HINT = "loginuserpass"  # matches TU Delft's SimpleSAMLphp form URL; adjust for your institution
CODE_INPUT_SELECTOR = (
    'input[name*="code" i], input[id*="code" i], '
    'input[autocomplete="one-time-code"], input[type="tel"]'
)


def _find_email_code() -> str | None:
    """Runs EMAIL_CODE_CHECK_COMMAND (if set in .env) and extracts a 4-8
    digit code from its stdout. This is a pluggable hook, not a built-in
    email client - point it at whatever you use to check your own inbox
    (an IMAP one-liner, a script hitting your provider's API, etc); this
    project has no opinion on how you read your email. Unset by default,
    in which case scripted_login just fails loudly if a step-up challenge
    appears, rather than guessing at how to fetch a code."""
    if not cfg.email_code_check_command:
        return None
    try:
        result = subprocess.run(
            cfg.email_code_check_command, shell=True, capture_output=True, text=True, timeout=90,
        )
    except Exception as e:
        print(f"[browser_session] EMAIL_CODE_CHECK_COMMAND failed: {e}")
        return None
    m = re.search(r"\b(\d{4,8})\b", result.stdout)
    return m.group(1) if m else None


def bootstrap_login_scripted() -> bool:
    """Fully automated login using BRIGHTSPACE_USERNAME/BRIGHTSPACE_PASSWORD
    from .env - no human, no display needed for the common case (an
    institution whose Brightspace login itself doesn't require MFA).

    The form selectors below (#username/#password/#submit_button) are
    confirmed live against TU Delft's login.tudelft.nl (a SimpleSAMLphp
    login page, no WAYF step). Other institutions - especially ones on a
    different identity provider - will likely need different selectors;
    if this raises immediately with an "unexpected page" error, dump the
    real login page's HTML and adjust the selectors below rather than
    assuming this is portable as-is. The overall shape (fill form, submit,
    check whether you landed back on Brightspace, otherwise look for a
    code field and try the pluggable email-code hook) should generalize
    even where the exact selectors don't.

    Raises RuntimeError on any failure, with the unexpected page saved to
    disk for inspection - falls back to bootstrap_login.py (interactive)
    from there, doesn't retry or guess further."""
    if not cfg.brightspace_username or not cfg.brightspace_password:
        raise RuntimeError(
            "BRIGHTSPACE_USERNAME / BRIGHTSPACE_PASSWORD not set in .env - "
            "can't run a scripted login without them."
        )
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.goto(cfg.brightspace_base_url, wait_until="networkidle")

        if LOGIN_FORM_URL_HINT not in page.url:
            browser.close()
            raise RuntimeError(
                f"Expected a login form (url containing {LOGIN_FORM_URL_HINT!r}) but landed on "
                f"{page.url!r} instead - your institution's login flow likely differs from TU "
                f"Delft's. Inspect the real page and adjust this function's selectors."
            )

        page.fill("#username", cfg.brightspace_username)
        page.fill("#password", cfg.brightspace_password)
        with page.expect_navigation(wait_until="networkidle"):
            page.click("#submit_button")

        if page.url.startswith(cfg.brightspace_base_url):
            context.storage_state(path=str(STORAGE_STATE_FILE))
            browser.close()
            print(f"[browser_session] scripted login succeeded, session saved to {STORAGE_STATE_FILE}")
            return True

        # Didn't land back on Brightspace - a step-up challenge, bad
        # credentials, or an account notice appeared instead.
        code_input = page.locator(CODE_INPUT_SELECTOR).first
        if code_input.count() > 0:
            code = _find_email_code()
            if code:
                code_input.fill(code)
                submit = page.locator('button[type="submit"], input[type="submit"]').first
                if submit.count() > 0:
                    with page.expect_navigation(wait_until="networkidle"):
                        submit.click()
                if page.url.startswith(cfg.brightspace_base_url):
                    context.storage_state(path=str(STORAGE_STATE_FILE))
                    browser.close()
                    print(f"[browser_session] scripted login (with email code) succeeded, "
                          f"session saved to {STORAGE_STATE_FILE}")
                    return True

        debug_path = SESSION_DIR / "scripted_login_unexpected_page.html"
        debug_path.write_text(page.content())
        current_url = page.url
        browser.close()
        raise RuntimeError(
            f"Scripted login did not reach Brightspace as expected - stopped at {current_url!r}. "
            f"Dumped the page to {debug_path} for inspection. Run scripts/bootstrap_login.py "
            f"interactively instead (needs a real display for whatever step this is)."
        )


def open_context(playwright, headless: bool = True) -> BrowserContext:
    if not has_saved_session():
        raise RuntimeError(
            "No saved Brightspace session - run `python scripts/bootstrap_login.py` "
            "interactively first (needs a real display for the login/MFA step)."
        )
    browser = playwright.chromium.launch(headless=headless)
    return browser.new_context(storage_state=str(STORAGE_STATE_FILE))
