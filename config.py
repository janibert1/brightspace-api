"""Loads .env (repo root) into a plain namespace. No external dependency -
just a plain KEY=VALUE line parser, real environment variables win over the
file so this also works fine in a container/systemd EnvironmentFile setup
without editing this module at all.
"""
import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent / ".env"


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
        env = {**_load_env(ENV_FILE), **os.environ}  # real env vars win over .env

        self.brightspace_base_url = env.get("BRIGHTSPACE_BASE_URL", "").rstrip("/")
        if not self.brightspace_base_url:
            raise RuntimeError(
                "BRIGHTSPACE_BASE_URL is not set - copy .env.example to .env and fill it in "
                "(e.g. https://brightspace.tudelft.nl, or your own institution's D2L domain)."
            )


cfg = Config()
