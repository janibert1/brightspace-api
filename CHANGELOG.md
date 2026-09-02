# Changelog

Every entry here reflects something actually confirmed live against a
real Brightspace account, not a guess — see the corresponding method's
own docstring in `client.py`/`session.py` for the full technical detail
behind each line.

## 0.6.3

- Fixed `get_external_link()` 500ing on any Link-type topic whose TOC
  `Url` is already a full absolute URL (a plain "External Resource"
  pointing straight at a third-party page, as opposed to a
  Brightspace-internal `quickLink.d2l?...` launcher). The code
  unconditionally prepended `base_url` onto `topic["url"]`, producing a
  malformed glued-together URL (e.g.
  `https://brightspace.tudelft.nlhttps://arendschwab.com/...`) that
  `page.goto()` rejects outright — surfaced as an unhandled exception,
  a bare 500 from the FastAPI wrapper with no useful detail. Found via
  a real course-audit run hitting a textbook download link stored this
  way. Fixed with the same `url if url.startswith("http") else
  f"{base_url}{url}"` guard `get_notification_detail()` already uses.
  Confirmed live: the same request now correctly resolves to
  `https://arendschwab.com/teaching/advdynbook/`.

## 0.6.2

- Fixed `get_deadlines()`: title/href pairing was off by one for every
  item on the list. The Work-To-Do widget's `action_href` appears
  BEFORE its own title in DOM walk order, not after — the pairing logic
  assumed the opposite, so every url silently pointed at the WRONG
  title (each item got the next item's real href, and the very first
  href was dropped entirely). Real live impact: "Assignment A" was
  pointing at Assignment B's actual folder, B at C's, and so on down
  the whole list — confirmed against each folder's own real `<h1>`
  title before concluding this was the bug and not a one-off fluke.

## 0.6.1

- New `get_notification_detail(url)`: the full body of one notification,
  given the `url` a `get_notifications()` item already has —
  `get_notifications()` was always title/course/received-only by design,
  no way to read what an announcement actually says without a one-off
  scrape. Found chasing two real notifications that turned out to need
  real action. Same `d2l-html-block` `html`-attribute gotcha as
  `get_module_description()`/`get_announcements()` (the body lives in an
  HTML attribute string, not real DOM text — invisible to
  `inner_text()`/`text_content()`, visible in a screenshot or
  `page.content()`). Also fixes a real false-positive: the detail page
  also has two hidden `<h2>`s belonging to the idle-session-timeout
  modal ("Are You Still There?") — a naive `"h1, h2"` title selector
  picks those up first instead of the real `<h1>` title.

## 0.6.0

TU Delft rolled out a newer content UI ("Lessons") as the default for
every current course — silently broke every content/module/download
method for anything actually being taken right now (an old, finished
course on the classic UI kept working, which hid this at first).

- `get_course_content()`, `get_course_modules()`, `get_module_description()`,
  `get_module_content()`, `get_all_course_content()`, `get_external_link()`,
  `download_course_file()`: all rewritten to use Brightspace's own real
  REST API (`GET /d2l/api/le/unstable/<ou>/content/toc`) instead of
  scraping either UI's DOM — confirmed live this API is the shared data
  source both the old and new UI render from, so it works identically
  for both, and won't break again the next time TU Delft changes the
  frontend.
- Real side effect, not just a fix: `get_course_content()` now returns
  the WHOLE course (every module, however nested) in one fast API call
  — the old "only sees whichever module happens to be selected"
  limitation is gone, not just documented-around anymore.
- `file_type` on every content item is now Brightspace's own
  `TypeIdentifier` (`"File"`, `"Link"`, etc), not the old scraped
  anchor-title string — a real value change for anything matching on
  the old strings.
- `dedupe` params on `get_module_content()`/`get_all_course_content()`
  are now no-ops (nothing left to dedupe against a real structured API)
  — kept only so existing call sites don't break.
- Found while fixing this: `get_external_link()` on a `TypeIdentifier:
  "Link"` (LTI) topic can resolve straight through to the real
  destination with zero extra login — confirmed live against an actual
  quiz topic, landed cleanly on `ans.app/digital_test/assignments/.../results/new`.

## 0.5.0

Fixes to real, previously-documented limitations, plus more docs.

- `get_course_modules()`: excludes fixed navigational tabs ("Overview",
  "Bookmarks", "Course Schedule", "Table of Contents") by default —
  they're not real content modules, just always-present UI tabs living
  in the same tree. `include_navigation_tabs=True` restores the old
  behavior.
- `get_module_content()`: new `dedupe=True` default. A folder with
  sub-folders doesn't reliably show a distinct "just this folder" view
  when clicked — it can include some or all of a sub-folder's own items
  too, producing confusing near-duplicates. Dedupe keeps only the
  deepest occurrence of each `topic_id`. Heuristic, not proven-correct —
  `dedupe=False` gets the raw data back.
- `get_external_link()`: added `likely_requires_separate_login`, a
  best-effort flag (URL/title pattern match against common login-page
  conventions) for the case where the resolved destination is actually
  another service's own SSO wall rather than real content.
- New `get_all_course_content(ou)`: aggregates every module's full
  content (via `get_course_modules()` + `get_module_content()`) into one
  list — a genuine "show me the whole course" call, not a workaround.
  Slow (one request per module/sub-folder) by nature, documented as such.
- Re-checked Quizzes against all 16 org units this account has access
  to (previously checked 4) — still zero quiz data anywhere, confirmed
  this stays a real gap, not a shortcut.
- Added this changelog.

## 0.4.0

- New `get_module_content(ou, name)`: walks a named module's full nested
  subtree (Content modules can nest folders 3+ levels deep — confirmed
  live: a Week module → a lecture sub-folder → a further "Werkcollege"
  sub-folder — and `get_course_content()` only ever sees whichever one
  happens to already be selected). Root-caused from a real user report
  of a topic that seemed to not exist anywhere.
- New `get_external_link(ou, topic_id)`: resolves an "External
  Resource"/"External Learning Tool" topic's real destination by
  actually clicking its "Open in New Window" button (the URL isn't a
  static href — Brightspace resolves it via JS at click time). Confirmed
  live against two different real cases: an LTI-launched tool resolving
  cleanly with no extra login, and a plain external link landing on a
  separate service's own SSO wall instead.

## 0.3.0

- New `get_course_modules(ou)` / `get_module_description(ou, name)`: a
  module's own description/overview text (e.g. weekly "Leerdoelen"
  blocks) — the Content tree is JS-driven with no per-module URL, so
  loading a specific module's text means actually clicking it.
- New `get_notifications()`: the homepage's "Update alerts" bell — a
  cross-course announcement/grade-update feed. Found there are two
  different bell icons ("Subscription alerts" and "Update alerts") with
  distinct behavior; a loose selector would silently grab the wrong,
  empty one.
- `upload()`: was a stub, now real. Full flow (Add a File → file-picker
  iframe → My Computer tab → native file chooser → confirm staging)
  confirmed live with a real test file against a real dropbox.
  `confirm_submit` defaults `False` — the final Submit click was
  deliberately never exercised live against a real assignment.

## 0.2.0

- Restructured from a flat FastAPI-only script collection into a real
  installable package (`src/` layout, `pyproject.toml`). Added
  `BrightspaceClient`, a synchronous class usable directly as a library,
  not just over HTTP — the FastAPI server became a thin wrapper around
  it instead of a separate implementation.
- Console scripts: `brightspace-login`, `brightspace-login-scripted`,
  `brightspace-serve`.
- Config now defaults to `~/.brightspace-api/` for session/downloads
  instead of paths relative to a script checkout.

## 0.1.0 (initial release)

- `BrightspaceClient`-equivalent read endpoints (as a FastAPI app):
  courses, content, download, grades, assignments, discussions,
  announcements, deadlines.
- `bootstrap_login_scripted()`: fully automated login using stored
  credentials — confirmed live that Brightspace's own login has no MFA
  at TU Delft (only Microsoft 365/Outlook does there), so this works
  headless with no human needed for the common case. Falls back to
  detecting a step-up challenge and failing loudly rather than guessing.
- `enroll()`/`upload()` shipped as unimplemented stubs.
