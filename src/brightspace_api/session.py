"""Session persistence + login for Brightspace (D2L), so you don't have
to fight your institution's SSO/MFA on every single request.

Two ways to get a saved session:

- `bootstrap_login_interactive()` - opens a real headed browser, you log
  in yourself (including MFA/CAPTCHA, whatever your institution
  requires), the resulting session gets saved. Needs a real display.
  There's no way to script around a real MFA/CAPTCHA challenge from the
  outside, so this is the honest, general-purpose answer for any
  institution.

- `bootstrap_login_scripted()` - fully automated, no display or human
  needed, for institutions where the Brightspace login itself doesn't
  require MFA (confirmed true for TU Delft: MFA there guards Microsoft
  365/Outlook, not Brightspace/D2L). Detects if it didn't land back on
  Brightspace and fails loudly with the unexpected page saved to disk,
  rather than hanging or guessing - fall back to the interactive version
  in that case.

Either way, once a session is saved, `BrightspaceClient` (see client.py)
reuses it headlessly for every request until it eventually expires - see
the package README's "Session lifetime" section for what's actually
known about how long that takes.
"""
import re
import subprocess

from playwright.sync_api import sync_playwright

from .config import cfg

LOGIN_FORM_URL_HINT = "loginuserpass"  # matches TU Delft's SimpleSAMLphp form URL; adjust for your institution
CODE_INPUT_SELECTOR = (
    'input[name*="code" i], input[id*="code" i], '
    'input[autocomplete="one-time-code"], input[type="tel"]'
)


def has_saved_session() -> bool:
    return cfg.storage_state_file.exists()


def bootstrap_login_interactive():
    """Run this once, by hand, on a machine with a real display (or over
    VNC/X-forwarding) - NOT from cron. Opens a real headed browser so you
    can complete login (including MFA/CAPTCHA, whatever your institution
    requires) yourself, then saves the resulting session."""
    base_url = cfg.require_base_url()
    cfg.storage_state_file.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(base_url)
        print("Log in (including MFA/CAPTCHA if prompted) in the opened browser window, "
              "then come back here and press Enter.")
        input()
        context.storage_state(path=str(cfg.storage_state_file))
        browser.close()
    print(f"Session saved to {cfg.storage_state_file}")


def _find_email_code() -> str | None:
    """Runs EMAIL_CODE_CHECK_COMMAND (if set) and extracts a 4-8 digit
    code from its stdout. Pluggable hook, not a built-in email client -
    point it at whatever you use to check your own inbox (an IMAP
    one-liner, a script hitting your provider's API, etc). Unverified
    against a real challenge page (TU Delft's Brightspace login has none
    to test against) - treat it as a reasonable starting point, not a
    proven feature. Unset by default, in which case the scripted login
    just fails loudly if a step-up challenge appears."""
    if not cfg.email_code_check_command:
        return None
    try:
        result = subprocess.run(
            cfg.email_code_check_command, shell=True, capture_output=True, text=True, timeout=90,
        )
    except Exception as e:
        print(f"[brightspace_api.session] EMAIL_CODE_CHECK_COMMAND failed: {e}")
        return None
    m = re.search(r"\b(\d{4,8})\b", result.stdout)
    return m.group(1) if m else None


def bootstrap_login_scripted() -> bool:
    """Fully automated login using BRIGHTSPACE_USERNAME/BRIGHTSPACE_PASSWORD
    - no human, no display needed for the common case (an institution
    whose Brightspace login itself doesn't require MFA).

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
    disk for inspection - falls back to bootstrap_login_interactive from
    there, doesn't retry or guess further.

    Returns True on success (session saved)."""
    base_url = cfg.require_base_url()
    if not cfg.brightspace_username or not cfg.brightspace_password:
        raise RuntimeError(
            "BRIGHTSPACE_USERNAME / BRIGHTSPACE_PASSWORD are not set - "
            "can't run a scripted login without them."
        )
    cfg.storage_state_file.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.goto(base_url, wait_until="networkidle")

        if LOGIN_FORM_URL_HINT not in page.url:
            browser.close()
            raise RuntimeError(
                f"Expected a login form (url containing {LOGIN_FORM_URL_HINT!r}) but landed on "
                f"{page.url!r} instead - your institution's login flow likely differs from TU "
                f"Delft's. Inspect the real page and adjust session.py's selectors/URL hint."
            )

        page.fill("#username", cfg.brightspace_username)
        page.fill("#password", cfg.brightspace_password)
        with page.expect_navigation(wait_until="networkidle"):
            page.click("#submit_button")

        if page.url.startswith(base_url):
            context.storage_state(path=str(cfg.storage_state_file))
            browser.close()
            print(f"[brightspace_api.session] scripted login succeeded, session saved to {cfg.storage_state_file}")
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
                if page.url.startswith(base_url):
                    context.storage_state(path=str(cfg.storage_state_file))
                    browser.close()
                    print(f"[brightspace_api.session] scripted login (with email code) succeeded, "
                          f"session saved to {cfg.storage_state_file}")
                    return True

        debug_path = cfg.storage_state_file.parent / "scripted_login_unexpected_page.html"
        debug_path.write_text(page.content())
        current_url = page.url
        browser.close()
        raise RuntimeError(
            f"Scripted login did not reach Brightspace as expected - stopped at {current_url!r}. "
            f"Dumped the page to {debug_path} for inspection. Run bootstrap_login_interactive() "
            f"instead (needs a real display for whatever step this is)."
        )


def cli_bootstrap_interactive():
    """Entry point for the `brightspace-login` console script."""
    bootstrap_login_interactive()


def cli_bootstrap_scripted():
    """Entry point for the `brightspace-login-scripted` console script."""
    bootstrap_login_scripted()
