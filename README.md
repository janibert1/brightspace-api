# brightspace-api

A local FastAPI wrapper around a persistent Playwright browser session for
Brightspace (D2L). Built against TU Delft's install, but the pages it
scrapes are D2L's own stock templates rather than TU Delft-specific
customizations, so it likely works against other Brightspace/D2L
institutions with little or no change — not verified elsewhere yet, so
treat that as a reasonable bet rather than a guarantee.

Why this exists: Brightspace has no public student-facing API for most of
this data. This logs in once as a real browser (so it goes through
whatever SSO/MFA your institution requires, exactly like a human would),
saves the session, and reuses it headlessly for every request after that.

## What it gives you

| Endpoint | What it returns |
|---|---|
| `GET /api/courses` | Your enrolled/visible courses (org unit id + name) |
| `GET /api/courses/{ou}/content` | Files/topics in a course's Content area (see caveat below) |
| `GET /api/courses/{ou}/content/{topic_id}` *(download)* | Downloads a content file (PDF-backed topics only so far) |
| `GET /api/courses/{ou}/grades` | Grade items + scores + written feedback |
| `GET /api/courses/{ou}/assignments` | Assignment/Dropbox folders: due dates, submission status, score, feedback link |
| `GET /api/courses/{ou}/discussions` | Forum topics per course: thread/post counts, unread flag |
| `GET /api/announcements` | Recent announcements across your courses |
| `GET /api/deadlines` | The "Work To Do" widget — what's currently pending |
| `GET /healthz` | `{"status": "ok", "session_loaded": true/false}` |
| `POST /api/enroll`, `POST /api/upload` | Stubs, not implemented — see `main.py`, selectors need to be filled in against your own install before these do anything real |

Every endpoint's real DOM shape (what was actually found live, not
guessed) is documented in a comment directly above it in `main.py` — read
those before extending anything, they explain *why* each selector is
what it is.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium

cp .env.example .env
# edit .env - set BRIGHTSPACE_BASE_URL to your institution's Brightspace domain

.venv/bin/python scripts/bootstrap_login.py
# opens a real browser window - log in yourself (including MFA if prompted),
# press Enter in the terminal once you're logged in, session gets saved

.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
```

The bootstrap step needs a real display (a desktop session, or `ssh -X`,
or a VNC session on a headless box) — there's no way around this for a
real institutional login, and this project doesn't try to fake one.

### Fully automated login (no display, no human)

If *your* institution's Brightspace login doesn't itself require MFA —
confirmed true for TU Delft: logging into Brightspace goes straight
through with no challenge, MFA only guards Microsoft 365/Outlook there,
not D2L — you can skip the interactive step entirely:

```bash
# in .env: set BRIGHTSPACE_USERNAME and BRIGHTSPACE_PASSWORD
.venv/bin/python scripts/scripted_login.py
```

This drives the real TU Delft login form (`#username`/`#password`/
`#submit_button`) headlessly and saves the session, same as the
interactive flow. Safe to run from cron to refresh the session
periodically. If your institution's login *does* require MFA, or uses a
different identity provider than TU Delft's, this will detect that it
didn't land back on Brightspace and raise a clear error (with the
unexpected page saved to disk) rather than hang or guess — fall back to
`bootstrap_login.py` in that case, and if you want to adapt
`scripted_login` to your own institution, start from that saved page and
adjust the selectors in `browser_session.py`.

There's a pluggable, optional fallback (`EMAIL_CODE_CHECK_COMMAND` in
`.env`) for institutions where login *does* step up to an emailed
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

Practical takeaway: if something hits Brightspace at least once every
couple of hours, the session should keep renewing itself indefinitely. A
longer gap (going quiet overnight, say) risks the session having expired
by the time you next use it, at which point you'll need to re-run
`scripts/bootstrap_login.py` by hand. There's no CAPTCHA on this login
flow (at least on the TU Delft install) — only real institutional
MFA — so a scripted *keepalive* (a cheap periodic request to keep the
idle timer from firing) is plausible future work if the 3-hour window
turns out to be a real problem in practice; a scripted *re-login* past
MFA is not, and this project doesn't attempt it.

## Known limitations

- **Content listing isn't a stable "show everything" view.** Brightspace
  remembers, server-side, per-user-per-course, whichever module you last
  had open in the Content tool, and only shows that module's items on a
  plain page load. A never-before-visited course tends to show a
  reasonably full listing; one you've already browsed into a specific
  module for will only show that module until a real browser session
  navigates elsewhere. For a course with many modules, the reliable way
  to see everything is a real interactive session (browse Content
  yourself once to find topic IDs across all modules), then use the
  download endpoint with those IDs directly.
- **Quizzes and Calendar are not implemented.** Quizzes: no course this
  was tested against had any quiz data to build a real parser against —
  don't guess at a DOM you haven't seen populated, dump the page and
  build against that if you have real data. Calendar is a month-grid
  widget, not a list — real event data means clicking through day cells
  and waiting for async popovers per day, a much bigger scrape than
  everything else here; `/api/deadlines` already covers "what's due
  soon" in a real list format.
- **`/api/enroll` and `/api/upload` are unimplemented stubs.** They exist
  as a shape to fill in, not working code — Brightspace's real
  enrollment and dropbox-submission UIs need their selectors confirmed
  live before these do anything.
- No built-in write-safety/approval gate on the two write-ish endpoints
  above — don't expose this service beyond localhost without adding your
  own confirmation step in front of them.
