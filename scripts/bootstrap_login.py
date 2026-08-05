#!/usr/bin/env python3
"""Run this by hand, ONCE, whenever the saved Brightspace session expires:

    python scripts/bootstrap_login.py

Needs a real display. If running this over SSH from a machine without one,
use `ssh -X` (X11 forwarding), or run it locally on a machine with a
screen, or drive it over a VNC session on a headless box - headless=False
genuinely needs somewhere to draw a window.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from browser_session import bootstrap_login_interactive

if __name__ == "__main__":
    bootstrap_login_interactive()
