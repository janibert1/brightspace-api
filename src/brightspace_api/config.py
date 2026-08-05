"""Config loading. No external dependency (no python-dotenv) - a plain
KEY=VALUE line parser is enough here, and real environment variables
always win over any .env file, so this also works fine in a container or
systemd EnvironmentFile setup without touching this module at all.

Looks for a `.env` file in the current working directory by default (the
usual convention for a project checkout) - set BRIGHTSPACE_ENV_FILE to
point at a specific file instead (useful once this is pip-installed and
you're running it from somewhere that isn't the repo itself).
"""
import os
from pathlib import Path


def _load_env(path: Path) -> dict:
    values = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip()
    return values


class Config:
    def __init__(self):
        env_file = Path(os.environ.get("BRIGHTSPACE_ENV_FILE", ".env"))
        env = {**_load_env(env_file), **os.environ}  # real env vars win over the file

        def get(key: str, default: str = "") -> str:
            # Treat a present-but-empty value (e.g. a blank line left over
            # from .env.example, `KEY=`) the same as unset, not as an
            # explicit empty override - otherwise every optional key in
            # .env.example would silently defeat its own default the
            # moment someone copies the file without filling it in.
            val = env.get(key, "")
            return val if val else default

        self.brightspace_base_url = get("BRIGHTSPACE_BASE_URL").rstrip("/")

        # Only needed for the fully-automated login (BrightspaceClient's
        # bootstrap_login_scripted / the `brightspace-login-scripted` CLI) -
        # the interactive login doesn't need these at all.
        self.brightspace_username = get("BRIGHTSPACE_USERNAME")
        self.brightspace_password = get("BRIGHTSPACE_PASSWORD")

        # Optional: a shell command that prints a one-time verification code
        # to stdout, for the (unverified - see session.py) email-code
        # fallback in the scripted login. Leave unset to skip that fallback
        # entirely and just fail loudly if a step-up challenge appears.
        self.email_code_check_command = get("EMAIL_CODE_CHECK_COMMAND")

        # Where the saved session (cookies) lives. Defaults to a per-user
        # location rather than anywhere inside this package, since once
        # installed via pip this code doesn't live in a writable, personal
        # checkout anymore. Override to point multiple installs/services at
        # the same already-logged-in session instead of re-doing login.
        default_storage = Path.home() / ".brightspace-api" / "storage_state.json"
        self.storage_state_file = Path(get("BRIGHTSPACE_STORAGE_STATE_FILE", str(default_storage)))

        # Where downloaded course files get cached.
        default_downloads = Path.home() / ".brightspace-api" / "downloads"
        self.downloads_dir = Path(get("BRIGHTSPACE_DOWNLOADS_DIR", str(default_downloads)))

    def require_base_url(self) -> str:
        if not self.brightspace_base_url:
            raise RuntimeError(
                "BRIGHTSPACE_BASE_URL is not set - copy .env.example to .env (or set the real "
                "environment variable) and fill in your institution's Brightspace domain, e.g. "
                "https://brightspace.tudelft.nl."
            )
        return self.brightspace_base_url


cfg = Config()
