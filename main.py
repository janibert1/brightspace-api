"""Local FastAPI wrapper around a Playwright Brightspace (D2L) session.
Run with: uvicorn main:app --host 127.0.0.1 --port 8000
(see README.md for setup, including the one-time interactive login step)

2026-08-05: /api/announcements and /api/deadlines filled in against the
real live TU Delft Brightspace DOM (found via a bootstrapped session).

Two very different DOM shapes on this D2L install:
- Announcements (/d2l/lms/news/main.d2l) is old-school server-rendered
  HTML: a <table summary="List of announcements">, each item as a pair of
  <tr> (title row, then a "d_detailsRow" body row). Plain CSS selectors
  work fine.
- Deadlines (/d2l/le/worktodo/view, the "Work To Do" widget) is a MODERN
  Lit-based web-component tree, 5+ levels of nested shadow DOM
  (d2l-w2d-work-to-do > d2l-w2d-collections > d2l-w2d-list >
  d2l-w2d-list-item-assignment > ...), each level fetching its own data
  async from *.activities.api.brightspace.com. That API looked cleaner to
  hit directly, but the token-receiver auth model wasn't worth reverse
  engineering for this - instead, WALK_SHADOW_JS below pierces every
  shadow boundary generically (Playwright's own locator engine pierces
  *open* shadow roots for plain CSS, but reading raw attributes like the
  assignment title - stored in a custom `_label` attribute, not text
  content - needed a real DOM walk via page.evaluate). This is more
  robust to D2L UI tweaks than hand-picking a selector at every one of
  those 5 levels, but if TU Delft ships a real redesign, re-run the
  exploration this comment describes (bootstrap a session, dump
  `el.shadowRoot.innerHTML` at each level) rather than guessing.

2026-08-05, added /api/courses + /api/courses/{ou}/content + download:
- Courses: another shadow-DOM widget (d2l-my-courses-v2), but the useful
  data is plain <a href="/d2l/home/<orgUnitId>">Course Name</a> anchors
  inside it - WALK_SHADOW_JS-style walk, filtered to that href shape.
- Per-course content listing (/d2l/le/content/<ou>/Home): old-school
  server-rendered again, real items are `a.d2l-link[href*="/viewContent/"]`
  with a `title` attribute shaped `'filename' - <File Type>`.
  **Known limitation, found the hard way**: this page is NOT a stable
  "show everything" view - D2L remembers, server-side, per-user-per-course,
  whichever module you last had open, and only shows that module's items
  on a plain load. A never-before-visited course tends to show a
  reasonably full listing; a course you've already browsed into a
  specific module for will only show that module until you (or a real
  browser session) navigate elsewhere. Clicking through the left-hand
  module tree to force a specific module open works but is exactly what
  causes this "sticks to last module" state in the first place - don't
  build an "expand everything" helper without expecting it to leave the
  account's Content view sitting on whatever module it last opened. For
  a course with many modules, the reliable way to see everything is the
  same noVNC remote-display session used for login - browse the real
  Content page as a human once to find topic IDs across all modules,
  then use the download endpoint below with those IDs directly.
- Download: a viewContent page renders the actual file inside an
  <iframe class="d2l-fileviewer-rendered-pdf"> whose `src` has a `file=`
  query param containing the real, direct, authenticated-session-only
  file URL (confirmed for PDFs; other types not yet tested - the D2L
  content-object context menu likely has an explicit "Download" action
  for those, but its menu items are lazily AJAX-loaded on click rather
  than present in the page HTML, not explored yet).

2026-08-05, same session, added /api/courses/{ou}/grades:
- /d2l/lms/grades/index.d2l?ou=<ou> redirects to the real grades page
  (my_grades/main.d2l) - old-school server-rendered again, a
  <table summary="List of grade items and their values">. Each row: a
  <label> for the item name, a <span id="..."> for the grade (literal
  text "-%" when ungraded, "60 %" etc. once graded - kept as the raw
  string rather than parsed to a number, since ungraded/excused/dropped
  items all render as non-numeric text and a float-or-None split isn't
  obviously better for a caller than just handing back what the page
  says), and a per-item feedback block (a d2l-html-block with real
  written feedback in some cases - confirmed live, e.g. a grader's
  comment quoting a JSONDecodeError in a student's submitted answer
  file). Grade item names repeat in a very D2L way (multiple
  attempts/resits per assignment, e.g. "Deelopdracht 10a Transportschip
  poging 1" vs "Herkansing deelopdracht 10a het transportschip") -
  returned as a flat list in page order rather than trying to group
  attempts, since D2L's own naming is already the disambiguator and
  grouping logic would just be guessing at conventions that could differ
  per course/instructor.

2026-08-05, same session, added /api/courses/{ou}/assignments and
/api/courses/{ou}/discussions - see each function's own docstring for the
real DOM shape (both old-school server-rendered, same family as
grades/announcements above). Also explored but deliberately NOT built:
- Quizzes (/d2l/lms/quizzing/user/quizzes_list.d2l?ou=<ou>) loads fine but
  came back with zero quizzes on every course checked (MT1466, Wiskunde 2,
  Thermodynamica, Dynamica) - this program apparently doesn't use D2L's
  Quizzes tool at all. No real data to verify a parser against, so
  following this project's existing rule (see /api/enroll, /api/upload
  below): don't build against a DOM you haven't actually seen populated.
  If a course ever does show quizzes, dump the page HTML first and build
  against that, don't guess from the empty-state markup.
- Calendar (/d2l/le/calendar/<ou>) is a month-grid widget, not a list -
  getting real event data means clicking each day cell and waiting for an
  async popover per day, which is a much bigger and more fragile scrape
  than everything else here. /api/deadlines (the Work To Do widget)
  already covers the "what's due soon" use case in a real list format;
  Calendar would mostly add exam dates and other longer-horizon events.
  Worth building later with a real display session if that's ever needed,
  not attempted this pass.

2026-08-05, session-persistence finding (flagged, not acted on): the
site's own idle-timeout dialog states "Your session expires after 180
minutes of inactivity" (seen live on the Quizzes page chrome). Combined
with storage_state.json: 9 of the account's 11 real auth cookies
(d2lSessionVal, d2lSecureSessionVal, the Shibboleth _shibsession cookie,
the TU-IDP auth token, the SURFconext session cookie) are browser
"Session" type with NO client-side expiry at all - their actual lifetime
is entirely server-side and invisible from the cookie jar. Only two
cookies declare a real expiry client-side (`lang`, cosmetic; `ShibbolethSSO`,
a 1-year "remember this browser" cookie, NOT proof the active session
itself lasts that long). Practical read: as long as something hits
Brightspace at least once per <3h, the session likely survives
indefinitely; a gap longer than that risks needing a fresh interactive
login (scripts/bootstrap_login.py - no CAPTCHA on this login flow, only
real TU Delft MFA, so it still needs a human for that step). Not fixed
this pass (deliberately deprioritized, not forgotten) - if this becomes a real problem, the fix is a
cheap keepalive (e.g. hit /healthz's underlying session with a real
Brightspace request every ~2h) rather than anything to do with the code
above.
"""
import re
import urllib.parse
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from playwright.async_api import async_playwright
from pydantic import BaseModel

from browser_session import has_saved_session, STORAGE_STATE_FILE
from config import cfg

DOWNLOADS_DIR = Path(__file__).resolve().parent / "downloads"

_playwright = None
_browser = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _playwright, _browser
    _playwright = await async_playwright().start()
    if has_saved_session():
        _browser = await _playwright.chromium.launch(headless=True)
    else:
        _browser = None
        print("[brightspace-api] WARNING: no saved session - run scripts/bootstrap_login.py "
              "interactively first. Endpoints will 503 until then.")
    yield
    if _browser:
        await _browser.close()
    await _playwright.stop()


app = FastAPI(title="brightspace-api", lifespan=lifespan)


async def _new_page():
    if _browser is None:
        raise HTTPException(503, "No Brightspace session - run scripts/bootstrap_login.py first.")
    context = await _browser.new_context(storage_state=str(STORAGE_STATE_FILE))
    return context, await context.new_page()


@app.get("/api/announcements")
async def get_announcements():
    context, page = await _new_page()
    try:
        # /d2l/lms/news/main.d2l needs a real ?ou=<org unit id> or it 400s -
        # there's no "just show me mine" shortcut. Get it from the
        # homepage's own news link rather than hardcoding an org unit id
        # (it's account/install-specific, not a Brightspace constant).
        await page.goto(f"{cfg.brightspace_base_url}/d2l/home", wait_until="networkidle")
        news_link = page.locator('a[href*="/d2l/lms/news/main.d2l"]').first
        if await news_link.count() == 0:
            return []
        news_url = await news_link.get_attribute("href")
        await page.goto(f"{cfg.brightspace_base_url}{news_url}" if news_url.startswith("/") else news_url,
                         wait_until="networkidle")
        table = page.locator('table[summary="List of announcements"]')
        if await table.count() == 0:
            return []
        rows = await table.locator("tbody > tr").all()
        results = []
        # First row is the column-header row (Title / Start Date); after
        # that, each announcement is a (title row, details row) pair.
        i = 1
        while i < len(rows):
            title_row = rows[i]
            link = title_row.locator("a.d2l-link").first
            title, href, body = None, None, None
            if await link.count():
                title = (await link.inner_text()).strip()
                href = await link.get_attribute("href")
            if i + 1 < len(rows) and "d_detailsRow" in (await rows[i + 1].get_attribute("class") or ""):
                body_block = rows[i + 1].locator("d2l-html-block")
                if await body_block.count():
                    body = await body_block.get_attribute("html")
                i += 2
            else:
                i += 1
            if title:
                results.append({
                    "title": title,
                    "url": f"{cfg.brightspace_base_url}{href}" if href and href.startswith("/") else href,
                    "body_html": body,
                })
        return results
    finally:
        await context.close()


# Collects every <a href> under the given root, piercing shadow roots -
# used for /api/courses (see module docstring: real course links are
# plain <a href="/d2l/home/<id>"> anchors buried inside a shadow tree).
WALK_LINKS_JS = """
(root) => {
    const results = [];
    function walk(node) {
        if (!node) return;
        if (node.nodeType === Node.ELEMENT_NODE) {
            if (node.tagName === "A" && node.getAttribute("href")) {
                results.push({href: node.getAttribute("href"), text: node.textContent.trim()});
            }
            if (node.shadowRoot) walk(node.shadowRoot);
            for (const child of node.children || []) walk(child);
        } else if (node instanceof ShadowRoot || node instanceof DocumentFragment) {
            for (const child of node.children || []) walk(child);
        }
    }
    walk(root);
    return results;
}
"""


@app.get("/api/courses")
async def get_courses():
    context, page = await _new_page()
    try:
        await page.goto(f"{cfg.brightspace_base_url}/d2l/home", wait_until="networkidle")
        await page.wait_for_timeout(6000)  # my-courses widget fetches its own data async
        widget = page.locator("d2l-my-courses-v2")
        if await widget.count() == 0:
            return []
        links = await widget.evaluate(WALK_LINKS_JS)
        seen = {}
        for link in links:
            m = re.fullmatch(r"/d2l/home/(\d+)", link["href"])
            if m and link["text"]:
                # Link text is "<display name>, <code>+<year>+<period>" - only
                # strip that trailing code part (matches CODE+YYYY+N), don't
                # blindly split on the first comma since some real course
                # names contain one too (e.g. "Weerstand, Voorstuwing en...").
                name = re.sub(r",\s*\S+\+\d{4}\+\S+$", "", link["text"]).strip()
                seen.setdefault(m.group(1), name)
        return [{"org_unit_id": ou, "name": name, "url": f"{cfg.brightspace_base_url}/d2l/home/{ou}"}
                for ou, name in seen.items()]
    finally:
        await context.close()


@app.get("/api/courses/{org_unit_id}/content")
async def get_course_content(org_unit_id: str):
    """See module docstring's "Known limitation" note - this reflects
    whatever module the account currently has last-open for this course,
    not guaranteed to be every file in every module."""
    context, page = await _new_page()
    try:
        await page.goto(f"{cfg.brightspace_base_url}/d2l/le/content/{org_unit_id}/Home", wait_until="networkidle")
        await page.wait_for_timeout(2000)
        items = page.locator('a.d2l-link[href*="/viewContent/"]')
        count = await items.count()
        results = []
        for i in range(count):
            item = items.nth(i)
            href = await item.get_attribute("href")
            title_attr = await item.get_attribute("title") or ""
            m = re.match(r"/d2l/le/content/\d+/viewContent/(\d+)/View", href or "")
            file_type = title_attr.rsplit(" - ", 1)[-1] if " - " in title_attr else None
            results.append({
                "topic_id": m.group(1) if m else None,
                "title": (await item.inner_text()).strip(),
                "file_type": file_type,
                "url": f"{cfg.brightspace_base_url}{href}" if href and href.startswith("/") else href,
            })
        return results
    finally:
        await context.close()


@app.get("/api/courses/{org_unit_id}/assignments")
async def get_course_assignments(org_unit_id: str):
    """The Dropbox/Assignments tool (/d2l/lms/dropbox/user/folders_list.d2l)
    - old-school server-rendered, same family as grades/announcements.
    More granular than /grades for this specific data: has due dates,
    submission counts, and an explicit "Not Submitted" state (grades only
    shows a blank/"-%" for both "not submitted" and "submitted, ungraded",
    indistinguishable there). Row shape confirmed live against MT1466:
    a category header row (`tr.d_ggl2`, name in `label > span.ds_i`) followed
    by one row per assignment - name+due date inside a `<th>`, then 3 `<td>`s
    (submission status, score, feedback link). The name link's `title`
    attribute ("Submit files to <name>") is used over the link's own text,
    since the text sometimes has a group-name prefix (e.g. "PG10: ") the
    title doesn't. Score cell has several hidden `<label>`s plus one visible
    one when graded (same pattern as /grades) - all hidden means ungraded."""
    context, page = await _new_page()
    try:
        await page.goto(f"{cfg.brightspace_base_url}/d2l/lms/dropbox/user/folders_list.d2l?ou={org_unit_id}",
                         wait_until="networkidle")
        table = page.locator('table[summary="List of assignments for this course"]')
        if await table.count() == 0:
            return []
        rows = await table.locator("tbody > tr").all()
        results = []
        current_category = None
        for row in rows:
            row_class = await row.get_attribute("class") or ""
            if "d_ggl2" in row_class:
                label = row.locator("span.ds_i").first
                if await label.count():
                    current_category = (await label.inner_text()).strip()
                continue

            name_link = row.locator('a[href*="folder_submit_files.d2l"]').first
            if await name_link.count() == 0:
                continue  # not a data row (e.g. a stray header) - skip rather than guess

            title_attr = await name_link.get_attribute("title") or ""
            name = title_attr.removeprefix("Submit files to ").strip() or (await name_link.inner_text()).strip()

            due = None
            due_label = row.locator("th label:has-text('Due on')").first
            if await due_label.count():
                due = (await due_label.inner_text()).strip()

            cells = row.locator("td")
            cell_count = await cells.count()

            submission_status = None
            if cell_count >= 1:
                submission_status = (await cells.nth(0).inner_text()).strip() or None

            score = None
            if cell_count >= 2:
                labels = cells.nth(1).locator("label")
                for i in range(await labels.count() - 1, -1, -1):
                    text = (await labels.nth(i).inner_text()).strip()
                    if text:
                        score = text
                        break

            feedback_url = None
            if cell_count >= 3:
                fb_link = cells.nth(2).locator('a[href*="folder_user_view_feedback.d2l"]').first
                if await fb_link.count():
                    href = await fb_link.get_attribute("href")
                    feedback_url = f"{cfg.brightspace_base_url}{href}" if href and href.startswith("/") else href

            results.append({
                "category": current_category,
                "name": name,
                "due": due,
                "submission_status": submission_status,
                "score": score,
                "feedback_url": feedback_url,
            })
        return results
    finally:
        await context.close()


@app.get("/api/courses/{org_unit_id}/discussions")
async def get_course_discussions(org_unit_id: str):
    """Discussions tool (/d2l/le/<ou>/discussions/List) - a course can have
    multiple forums, each rendered as its own
    `table[summary="Topic List for Forum <name>"]` (confirmed live: MT1466
    has two, "Algemeen Forum" and "Project Groep discussie forum"). Real
    columns (from the table's own <thead>, confirmed live rather than
    guessed): Topic | Threads | Posts | Last Post. `has_unread` is read off
    the row's own `d2l-grid-unread` CSS class (not text-matched - an earlier
    version of this looked for a "Contains unread posts" string, but that
    text turned out to live in an offscreen a11y label on the *topic* cell,
    not near Threads/Posts/Last Post, so it never matched; the row class is
    the actual, reliable signal). Doesn't fetch individual post content (a real per-topic
    scrape, not attempted here) - this is a list of what threads exist,
    how active they are, and whether you have unread posts."""
    context, page = await _new_page()
    try:
        await page.goto(f"{cfg.brightspace_base_url}/d2l/le/{org_unit_id}/discussions/List",
                         wait_until="networkidle")
        await page.wait_for_timeout(1500)
        tables = page.locator('table[summary^="Topic List for Forum"]')
        forum_count = await tables.count()
        results = []
        for fi in range(forum_count):
            table = tables.nth(fi)
            summary = await table.get_attribute("summary") or ""
            forum_name = summary.removeprefix("Topic List for Forum ").strip()
            rows = await table.locator("tbody > tr").all()
            for row in rows:
                link = row.locator("a.d2l-link").first
                if await link.count() == 0:
                    continue
                title = (await link.inner_text()).strip()
                href = await link.get_attribute("href")
                if not title or not href:
                    continue

                row_class = await row.get_attribute("class") or ""
                cells = row.locator("td")
                cell_count = await cells.count()
                threads = (await cells.nth(0).inner_text()).strip() if cell_count >= 1 else None
                # Posts cell text is "N\nUnread for topic <name>:\n(M)" when unread posts exist -
                # that trailing part duplicates has_unread/topic below, keep just the leading count.
                posts_raw = (await cells.nth(1).inner_text()).strip() if cell_count >= 2 else None
                posts = posts_raw.split("\n", 1)[0].strip() if posts_raw else None
                last_post_text = (await cells.nth(2).inner_text()).strip() if cell_count >= 3 else ""

                results.append({
                    "forum": forum_name,
                    "topic": title,
                    "url": f"{cfg.brightspace_base_url}{href}" if href.startswith("/") else href,
                    "threads": threads,
                    "posts": posts,
                    "has_unread": "d2l-grid-unread" in row_class,
                    "last_post_text": last_post_text or None,  # raw D2L text (author/date, or restriction notes) - not split further, wording isn't stable enough to parse confidently
                })
        return results
    finally:
        await context.close()


@app.get("/api/courses/{org_unit_id}/grades")
async def get_course_grades(org_unit_id: str):
    context, page = await _new_page()
    try:
        await page.goto(f"{cfg.brightspace_base_url}/d2l/lms/grades/index.d2l?ou={org_unit_id}",
                         wait_until="networkidle")
        table = page.locator('table[summary="List of grade items and their values"]')
        if await table.count() == 0:
            return []
        rows = await table.locator("tbody > tr").all()
        results = []
        i = 1  # row 0 is the column-header row (Grade Item / Points / Grade / Comments)
        while i < len(rows):
            row = rows[i]
            label = row.locator("label").first
            if await label.count() == 0:
                i += 1
                continue
            name = (await label.inner_text()).strip()
            # The grade's own <span> has a generated id (no stable class) -
            # it's the only <span> inside the row's second <td>, so just
            # take whichever one isn't empty rather than guessing an id.
            grade_span = row.locator("td").nth(1).locator("span").first
            grade = (await grade_span.inner_text()).strip() if await grade_span.count() else None
            feedback_block = row.locator("d2l-html-block")
            feedback_html = await feedback_block.get_attribute("html") if await feedback_block.count() else None
            results.append({"item": name, "grade": grade, "feedback_html": feedback_html})
            i += 1
        return results
    finally:
        await context.close()


@app.get("/api/courses/{org_unit_id}/download/{topic_id}")
async def download_course_file(org_unit_id: str, topic_id: str):
    """Downloads a file by its topic id (from /content's "topic_id" field)
    to a local cache and returns it. PDF-backed topics only for now - see
    module docstring. Re-downloads are cheap to skip: if already cached
    locally, serves that copy instead of re-scraping."""
    context, page = await _new_page()
    try:
        view_url = f"{cfg.brightspace_base_url}/d2l/le/content/{org_unit_id}/viewContent/{topic_id}/View"
        await page.goto(view_url, wait_until="networkidle")
        await page.wait_for_timeout(1500)

        iframe = page.locator("iframe.d2l-fileviewer-rendered-pdf")
        if await iframe.count() == 0:
            raise HTTPException(
                501,
                "Only PDF-viewer-backed topics are downloadable so far (see brightspace-api/main.py "
                "module docstring) - this topic didn't render one, might be a non-PDF file type or "
                "an external tool link, not built against the real DOM yet.",
            )
        src = await iframe.get_attribute("src")
        parsed = urllib.parse.urlparse(src)
        file_param = urllib.parse.parse_qs(parsed.query).get("file", [None])[0]
        if not file_param:
            raise HTTPException(500, "PDF viewer iframe found but had no 'file=' param - unexpected shape.")
        file_url = f"{cfg.brightspace_base_url}{urllib.parse.unquote(file_param)}"

        filename = file_param.rsplit("/", 1)[-1].split("?")[0]
        filename = urllib.parse.unquote(filename) or f"{topic_id}.pdf"
        dest_dir = DOWNLOADS_DIR / org_unit_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / filename

        resp = await context.request.get(file_url)
        if resp.status != 200:
            raise HTTPException(502, f"Brightspace returned {resp.status} fetching the file itself.")
        dest_path.write_bytes(await resp.body())

        return FileResponse(str(dest_path), filename=filename, media_type="application/pdf")
    finally:
        await context.close()


# Pierces every open shadow root under the given root element, collecting
# (title, action_href) pairs plus whatever <h2>/<h3> section heading was
# most recently seen in document order - see module docstring for why
# this exists instead of per-level CSS selectors.
WALK_SHADOW_JS = """
(root) => {
    const results = [];
    let currentHeading = null;
    function walk(node) {
        if (!node) return;
        if (node.nodeType === Node.ELEMENT_NODE) {
            const tag = node.tagName ? node.tagName.toLowerCase() : "";
            if ((tag === "h2" || tag === "h3") && node.textContent.trim()) {
                currentHeading = node.textContent.trim();
            }
            const label = node.getAttribute && node.getAttribute("_label");
            if (label) results.push({type: "title", value: label, heading: currentHeading});
            const actionHref = node.getAttribute && node.getAttribute("action-href");
            if (actionHref) results.push({type: "action_href", value: actionHref, heading: currentHeading});
            if (node.shadowRoot) walk(node.shadowRoot);
            for (const child of node.children || []) walk(child);
        } else if (node instanceof ShadowRoot || node instanceof DocumentFragment) {
            for (const child of node.children || []) walk(child);
        }
    }
    walk(root);
    return results;
}
"""


@app.get("/api/deadlines")
async def get_deadlines():
    context, page = await _new_page()
    try:
        await page.goto(f"{cfg.brightspace_base_url}/d2l/le/worktodo/view", wait_until="networkidle")
        # The nested web components fetch their own data asynchronously
        # after the page itself finishes loading - networkidle doesn't
        # cover that inner fetch, so give it a moment.
        await page.wait_for_timeout(5000)
        widget = page.locator("d2l-w2d-work-to-do")
        if await widget.count() == 0:
            return []
        flat = await widget.evaluate(WALK_SHADOW_JS)

        # Titles and their action_href appear adjacent, in order, one pair
        # per work-item (confirmed against real data during setup - see
        # module docstring). Pair them up rather than assuming a 1:1 zip
        # in case some items are missing a link.
        results = []
        pending_title = None
        for entry in flat:
            if entry["type"] == "title":
                if pending_title:
                    results.append(pending_title)
                pending_title = {"title": entry["value"], "section": entry["heading"], "url": None}
            elif entry["type"] == "action_href" and pending_title is not None:
                pending_title["url"] = entry["value"]
        if pending_title:
            results.append(pending_title)
        return results
    finally:
        await context.close()


class EnrollRequest(BaseModel):
    course_codes: list[str]


@app.post("/api/enroll")
async def enroll(req: EnrollRequest):
    """Write endpoint - this function does NOT check any kind of approval
    or confirmation itself, so don't expose this port beyond localhost,
    and put your own confirmation step in front of it before calling."""
    context, page = await _new_page()
    try:
        results = {}
        for code in req.course_codes:
            # TODO: verify against real DOM - this whole flow (search ->
            # find course -> click enroll -> confirm) needs to be built
            # against the actual Brightspace course-enrollment UI.
            results[code] = "NOT_IMPLEMENTED - selectors need to be filled in against the real site"
        return results
    finally:
        await context.close()


class UploadRequest(BaseModel):
    assignment_url: str
    file_path: str


@app.post("/api/upload")
async def upload(req: UploadRequest):
    """Same "no built-in approval check" note as /api/enroll applies here."""
    context, page = await _new_page()
    try:
        await page.goto(req.assignment_url)
        # TODO: verify against real DOM - Brightspace's assignment
        # drop-box file input selector needs to be confirmed live.
        file_input = page.locator("input[type=file]").first
        await file_input.set_input_files(req.file_path)
        return {"status": "NOT_IMPLEMENTED - submit-button click needs the real selector too"}
    finally:
        await context.close()


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "session_loaded": _browser is not None}
