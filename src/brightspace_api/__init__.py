"""Python client + optional FastAPI server for Brightspace (D2L).

    from brightspace_api import BrightspaceClient

    with BrightspaceClient() as client:
        for course in client.get_courses():
            print(course["name"])

No saved session yet? Log in once:

    from brightspace_api.session import bootstrap_login_interactive
    bootstrap_login_interactive()

or, for institutions where Brightspace login itself doesn't need MFA
(see session.py's docstring):

    from brightspace_api.session import bootstrap_login_scripted
    bootstrap_login_scripted()

See README.md for the full endpoint/method list, setup, and known
limitations (especially the "Session lifetime" section).
"""
from .client import BrightspaceClient, BrightspaceError
from .config import cfg
from .session import (
    bootstrap_login_interactive,
    bootstrap_login_scripted,
    has_saved_session,
)

__all__ = [
    "BrightspaceClient",
    "BrightspaceError",
    "cfg",
    "bootstrap_login_interactive",
    "bootstrap_login_scripted",
    "has_saved_session",
]

__version__ = "0.5.0"
