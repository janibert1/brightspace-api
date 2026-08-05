#!/usr/bin/env python3
"""Fully automated Brightspace login - no display, no human needed for
institutions where Brightspace login itself doesn't require MFA (see
browser_session.py's docstring). Needs BRIGHTSPACE_USERNAME/
BRIGHTSPACE_PASSWORD set in .env:

    python scripts/scripted_login.py

Safe to run from cron/systemd (headless). If your institution's login
does step up to MFA, this fails with a clear error and a saved copy of
the unexpected page rather than hanging - use scripts/bootstrap_login.py
(interactive) in that case instead.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from browser_session import bootstrap_login_scripted

if __name__ == "__main__":
    bootstrap_login_scripted()
