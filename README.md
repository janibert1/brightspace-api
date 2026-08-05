# brightspace-api

A Python client (plus an optional local HTTP server) for Brightspace
(D2L), backed by a persistent Playwright browser session. Built against
TU Delft's install, but the pages it scrapes are D2L's own stock
templates rather than TU Delft-specific customizations, so it likely
works against other Brightspace/D2L institutions with little or no
change — not verified elsewhere yet, so treat that as a reasonable bet
rather than a guarantee.

Why this exists: Brightspace has no public student-facing API for most
of this data. This logs in once as a real browser (so it goes through
whatever SSO/MFA your institution requires, exactly like a human would),
saves the session, and reuses it headlessly for every call after that.

See `CHANGELOG.md` for what changed and when — every entry there reflects
something confirmed live against a real account, not a guess.

## Install

```bash
pip install .                    # from a checkout of this repo
# or, editable, for development:
pip install -e .
playwright install chromium

# only if you also want the local HTTP server:
pip install ".[server]"
```

## Quick start

```bash
cp .env.example .env
# edit .env - set BRIGHTSPACE_BASE_URL to your institution's Brightspace domain
```

```python
from brightspace_api import bootstrap_login_interactive, BrightspaceClient

bootstrap_login_interactive()  # opens a real browser window, log in yourself once

with BrightspaceClient() as client:
    for course in client.get_courses():
        print(course["org_unit_id"], course["name"])

    for a in client.get_course_assignments(course["org_unit_id"]):
        print(a["name"], a["due"], a["score"])
```

Or via the console scripts (installed with the package, no `python -c`
needed):

```bash
brightspace-login              # interactive, needs a real display
brightspace-login-scripted     # fully automated - see below
brightspace-serve              # runs the optional HTTP server on :8000
```

### A few more real examples

```python
with BrightspaceClient() as client:
    # A specific week's own description text (e.g. "Leerdoelen" blocks) -
    # the Content tree has no per-module URL, so this actually clicks
    # into the named module to load it.
    week = client.get_module_description("775932", "Week 1: Spanning en Rek")
    print(week["description_html"])

    # Everything under that module, including nested sub-folders -
    # get_course_content() alone would only see whichever one folder
    # happens to already be selected.
    for item in client.get_module_content("775932", "Week 1: Spanning en Rek"):
        print(item["folder_path"], item["title"])

    # Resolve an "External Resource"/"External Learning Tool" topic's
    # real destination (its "Open in New Window" button has no static
    # href - Brightspace resolves it via JS at click time).
    link = client.get_external_link("775932", "4630702")
    print(link["url"], "needs its own login?" , link["likely_requires_separate_login"])

    # The homepage's "Update alerts" bell - a cross-course feed of
    # announcements and grade updates.
    for n in client.get_notifications():
        print(n["type"], n["course"], n["title"])
```

## What it gives you

Every one of these exists as both a `BrightspaceClient` method and (with
the `[server]` extra installed) an HTTP endpoint on the same shape:

| `BrightspaceClient` method | `GET`/`POST` | What it returns |
|---|---|---|
| `get_courses()` | `/api/courses` | Your enrolled/visible courses (org unit id + name) |
| `get_course_content(ou)` | `/api/courses/{ou}/content` | Files/topics in a course's Content area (see caveat below) |
| `get_course_modules(ou)` | `/api/courses/{ou}/modules` | Top-level module names/ids in a course's Content tree (navigation tabs excluded by default) |
| `get_module_description(ou, name)` | `/api/courses/{ou}/modules/description?name=` | A specific module's own description/overview text (e.g. weekly "Leerdoelen" blocks) |
| `get_module_content(ou, name, dedupe=True)` | `/api/courses/{ou}/modules/content?name=` | Everything under a module, **including nested sub-folders**, deduplicated by default — see caveat below |
| `get_all_course_content(ou, dedupe=True)` | `/api/courses/{ou}/content/all` | Every module's full content, aggregated — genuinely "show me the whole course" (slow, see caveat below) |
| `download_course_file(ou, topic_id)` | `/api/courses/{ou}/download/{topic_id}` | Downloads a content file (PDF-backed topics only so far) |
| `get_external_link(ou, topic_id)` | `/api/courses/{ou}/content/{topic_id}/external-link` | Resolves an "External Resource"/"External Learning Tool" topic's real destination URL, plus a best-effort "did this need a separate login" flag |
| `get_course_grades(ou)` | `/api/courses/{ou}/grades` | Grade items + scores + written feedback |
| `get_course_assignments(ou)` | `/api/courses/{ou}/assignments` | Assignment/Dropbox folders: due dates, submission status, score, feedback link |
| `get_course_discussions(ou)` | `/api/courses/{ou}/discussions` | Forum topics per course: thread/post counts, unread flag |
| `get_announcements()` | `/api/announcements` | Recent announcements across your courses |
| `get_deadlines()` | `/api/deadlines` | The "Work To Do" widget — what's currently pending |
| `get_notifications()` | `/api/notifications` | The "Update alerts" bell — cross-course announcement/grade-update feed (first page only) |
| `enroll(course_codes)` | `/api/enroll` | Stub, not implemented |
| `upload(assignment_url, file_paths, confirm_submit=False)` | `/api/upload` | Submits file(s) to a Dropbox/Assignment folder — real, see caveat below |
| — | `/healthz` | `{"status": "ok", "session_loaded": true/false}` (server only) |

Every method's real DOM shape (what was actually found live, not
guessed) is documented in its own docstring in `client.py` — read those
before extending anything, they explain *why* each selector is what it
is.

`enroll` is a shape-only stub — Brightspace's real enrollment UI needs
its selectors confirmed live against your own institution before it does
anything real. `upload` IS implemented and confirmed live end-to-end
through staging a file, but its final step — actually clicking Submit —
was deliberately never exercised against a real assignment (a real,
essentially irreversible academic action) and is off by default
(`confirm_submit=False`). Read `upload`'s docstring before turning that
on. Neither endpoint has a built-in approval/confirmation check beyond
that flag; don't expose the HTTP server beyond localhost without adding
your own gate.

## Login

### Interactive (any institution)

```python
from brightspace_api import bootstrap_login_interactive
bootstrap_login_interactive()
```

Opens a real headed browser window, you log in yourself (including
MFA/CAPTCHA, whatever your institution requires), then the session gets
saved. Needs a real display (a desktop session, `ssh -X`, or a VNC
session on a headless box) — there's no way around this for a real
institutional login, and this project doesn't try to fake one.

### Fully automated (no display, no human)

```python
# in .env: set BRIGHTSPACE_USERNAME and BRIGHTSPACE_PASSWORD
from brightspace_api import bootstrap_login_scripted
bootstrap_login_scripted()
```

If *your* institution's Brightspace login doesn't itself require MFA —
confirmed true for TU Delft: logging into Brightspace goes straight
through with no challenge, MFA only guards Microsoft 365/Outlook there,
not D2L — this drives the real login form headlessly and saves the
session, same result as the interactive flow. Safe to run from cron to
refresh the session periodically.

The form selectors are confirmed live against TU Delft's
`login.tudelft.nl` (a SimpleSAMLphp login page). If your institution
uses a different identity provider, this will detect that it didn't land
back on Brightspace and raise a clear error (with the unexpected page
saved to disk) rather than hang or guess — fall back to the interactive
login in that case, and if you want to adapt this to your own
institution, start from that saved page and adjust the selectors in
`session.py`.

There's also a pluggable, optional fallback (`EMAIL_CODE_CHECK_COMMAND`
in `.env`) for institutions where login *does* step up to an emailed
one-time code specifically — point it at any command that prints the
code to stdout (an IMAP one-liner, your own inbox-checking script,
whatever you already have). This project has no built-in email client
and doesn't assume you have one; leave it unset to just fail loudly
instead. This fallback has not been exercised against a real challenge
page (TU Delft's Brightspace login has none to test against) — treat it
as a reasonable starting point, not a verified feature.

## Session lifetime (read this before relying on it)

Brightspace's own idle-timeout dialog states sessions expire after **180
minutes of inactivity**. Looking at the saved cookie jar directly: 9 of
the usual 11 real auth cookies (the D2L session cookies, the Shibboleth/
SSO session cookie, the identity-provider auth token) are browser
"Session"-type cookies with no client-declared expiry at all — their
actual lifetime is fully server-side and not visible from the cookie
file itself.

Practical takeaway: if something uses the client at least once every
couple of hours, the session should keep renewing itself indefinitely. A
longer gap (going quiet overnight, say) risks the session having expired
by the time you next use it, at which point `BrightspaceClient.start()`
will raise (`BrightspaceError: No saved Brightspace session`) — re-run
`bootstrap_login_interactive()` or `bootstrap_login_scripted()` and carry
on.

## Configuration

All via `.env` (in the current working directory by default — set
`BRIGHTSPACE_ENV_FILE` to point elsewhere) or real environment variables
(which always win). See `.env.example` for the full list:

- `BRIGHTSPACE_BASE_URL` — required.
- `BRIGHTSPACE_USERNAME` / `BRIGHTSPACE_PASSWORD` — only for the
  automated login.
- `EMAIL_CODE_CHECK_COMMAND` — optional, see above.
- `BRIGHTSPACE_STORAGE_STATE_FILE` / `BRIGHTSPACE_DOWNLOADS_DIR` —
  override where the session and downloaded files live. Default to
  `~/.brightspace-api/`. Point multiple installs/services at the same
  path to share one already-logged-in session instead of each doing its
  own login.

## Known limitations

Fixed where a real fix was possible; the rest are documented honestly
rather than guessed at. See `CHANGELOG.md` for exactly when/how each fix
landed.

**Fixed:**

- ~~Content listing isn't a stable "show everything" view~~ — fixed by
  `get_module_content()` (one module's full nested subtree) and
  `get_all_course_content()` (the whole course, every module). Plain
  `get_course_content()` still only reflects whichever module happens to
  already be selected — kept as-is since it's the fast/cheap option when
  that's all you need; reach for the other two when you actually need
  completeness.
- ~~`get_module_content()`'s near-duplicate folder-level results~~ —
  `dedupe=True` (the default) now collapses same-topic_id entries down to
  their deepest occurrence. This is a heuristic, not a proven-correct
  interpretation of D2L's own behavior (see the method's docstring for
  the actual evidence behind it) — pass `dedupe=False` for the raw,
  unfiltered data if you want to judge for yourself.
- ~~`get_external_link()` doesn't say whether it actually reached real
  content~~ — now returns a best-effort `likely_requires_separate_login`
  flag (checked against real login-page patterns: `login.`/`sso.`/`auth.`
  subdomains, "log in"/"sign in"/"inloggen" in the title). Only verified
  against one real institution's SSO page, so treat it as a hint to
  check manually, not a certainty either way.
- ~~`get_course_modules()` mixes real modules with fixed UI tabs~~ — now
  excludes "Overview"/"Bookmarks"/"Course Schedule"/"Table of Contents"
  by default (they never have a real `module_id`). Pass
  `include_navigation_tabs=True` for the old behavior back.

**Not fixed (genuinely not fixable right now, not just left undone):**

- **Quizzes are not implemented.** Re-checked against all 16 org units
  this account has access to (not just a sample) — zero quiz data
  anywhere to build or verify a parser against. Don't guess at a DOM
  nobody's ever seen populated; if you have a course that actually uses
  D2L's Quizzes tool, dump the page HTML and build against that for real.
- **Calendar is not implemented.** A month-grid widget, not a list — real
  event data means clicking through day cells and waiting for async
  popovers per day, a much bigger scrape than everything else here.
  `get_deadlines()` already covers "what's due soon" in a real list
  format; Calendar would mostly add exam dates and other longer-horizon
  events, not attempted.
- **`enroll()` is an unimplemented stub.** Enrolling in a real course is
  itself a real, consequential action — can't safely build and verify
  this against a live account without genuinely intending to enroll in
  something, unlike everything else here which is either read-only or
  (for `upload()`) has a safe default. Exists as a shape to fill in, not
  working code.

**Structural, not really "limitations" to fix:**

- The D2L homepage has two different bell icons —
  `get_notifications()` uses "Update alerts", not "Subscription alerts"
  (genuinely different feeds, confirmed live).
- **Threading, if you use the HTTP server**: `BrightspaceClient` uses
  Playwright's synchronous API, which must run on the same OS thread it
  was started on. `server.py` handles this internally (a dedicated
  single-worker thread pool) — if you build your own server or
  multi-threaded wrapper around `BrightspaceClient` directly, keep that
  constraint in mind rather than calling one instance from multiple
  threads.
