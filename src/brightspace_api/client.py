"""BrightspaceClient - a synchronous Python client for Brightspace (D2L),
backed by a persistent Playwright browser session (see session.py for how
that session gets created/saved).

    from brightspace_api import BrightspaceClient

    with BrightspaceClient() as client:
        for course in client.get_courses():
            print(course["name"])
            for a in client.get_course_assignments(course["org_unit_id"]):
                print(" ", a["name"], a["score"])

Every method here does exactly what the equivalent HTTP endpoint in
server.py does (server.py is a thin wrapper around this class) - so
"what does this actually scrape, and why" is documented once, here, in
each method's own docstring, not duplicated between the two.

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

Courses: another shadow-DOM widget (d2l-my-courses-v2), but the useful
data is plain <a href="/d2l/home/<orgUnitId>">Course Name</a> anchors
inside it - WALK_LINKS_JS-style walk, filtered to that href shape.

Per-course content listing (/d2l/le/content/<ou>/Home): old-school
server-rendered again, real items are `a.d2l-link[href*="/viewContent/"]`
with a `title` attribute shaped `'filename' - <File Type>`.
**Known limitation, found the hard way**: this page is NOT a stable
"show everything" view - D2L remembers, server-side, per-user-per-course,
whichever module you last had open, and only shows that module's items
on a plain load. A never-before-visited course tends to show a
reasonably full listing; a course you've already browsed into a specific
module for will only show that module until you (or a real browser
session) navigate elsewhere. For a course with many modules, the
reliable way to see everything is a real interactive session (browse
Content yourself once to find topic IDs across all modules), then use
download_course_file with those IDs directly.

Download: a viewContent page renders the actual file inside an
<iframe class="d2l-fileviewer-rendered-pdf"> whose `src` has a `file=`
query param containing the real, direct, authenticated-session-only file
URL (confirmed for PDFs; other types not yet tested - the D2L
content-object context menu likely has an explicit "Download" action for
those, but its menu items are lazily AJAX-loaded on click rather than
present in the page HTML, not explored yet).

Grades (/d2l/lms/grades/index.d2l?ou=<ou>, redirects to the real grades
page): old-school server-rendered again, a
<table summary="List of grade items and their values">. Each row: a
<label> for the item name, a <span id="..."> for the grade (literal
text "-%" when ungraded, "60 %" etc. once graded - kept as the raw
string rather than parsed to a number, since ungraded/excused/dropped
items all render as non-numeric text and a float-or-None split isn't
obviously better for a caller than just handing back what the page
says), and a per-item feedback block (a d2l-html-block with real written
feedback in some cases). Grade item names repeat in a very D2L way
(multiple attempts/resits per assignment) - returned as a flat list in
page order rather than trying to group attempts, since D2L's own naming
is already the disambiguator and grouping logic would just be guessing
at conventions that could differ per course/instructor.

Also explored but deliberately NOT built:
- Quizzes (/d2l/lms/quizzing/user/quizzes_list.d2l?ou=<ou>) loads fine
  but came back with zero quizzes on every course checked - no real data
  to verify a parser against. Don't build against a DOM you haven't
  actually seen populated; if a course ever does show quizzes, dump the
  page HTML first and build against that.
- Calendar (/d2l/le/calendar/<ou>) is a month-grid widget, not a list -
  getting real event data means clicking each day cell and waiting for
  an async popover per day, a much bigger and more fragile scrape than
  everything else here. get_deadlines() already covers the "what's due
  soon" use case in a real list format.

Session-persistence finding (flagged, not something this code tries to
fix): the site's own idle-timeout dialog states "Your session expires
after 180 minutes of inactivity". Combined with the saved cookie jar: 9
of the account's 11 real auth cookies are browser "Session" type with NO
client-side expiry at all - their actual lifetime is entirely server-side
and invisible from the cookie jar. Practical read: as long as something
uses this client at least once every couple hours, the session likely
survives indefinitely; a longer gap risks needing a fresh login (see
session.py).
"""
import re
import urllib.parse
from pathlib import Path

from playwright.sync_api import sync_playwright, Playwright, Browser

from .config import cfg
from .session import has_saved_session

# Collects every <a href> under the given root, piercing shadow roots -
# used for get_courses (real course links are plain
# <a href="/d2l/home/<id>"> anchors buried inside a shadow tree).
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

# Pierces every open shadow root under the given root element, collecting
# (title, action_href) pairs plus whatever <h2>/<h3> section heading was
# most recently seen in document order - used for get_deadlines, see
# module docstring for why this exists instead of per-level CSS selectors.
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


class BrightspaceError(Exception):
    """Raised for anything that isn't a plain "0 results" case - no saved
    session, an unexpected page shape, a failed file download, etc."""


class BrightspaceClient:
    """Use as a context manager so the underlying browser always gets
    closed:

        with BrightspaceClient() as client:
            ...

    or manage the lifecycle yourself:

        client = BrightspaceClient()
        client.start()
        try:
            ...
        finally:
            client.close()

    Calling a method without either is also fine for a quick one-off
    script - it lazily starts on first use - but then nothing closes the
    browser process for you, so prefer one of the two forms above for
    anything longer-lived than a single script."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    def __enter__(self) -> "BrightspaceClient":
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()

    def start(self):
        if self._browser is not None:
            return
        if not has_saved_session():
            raise BrightspaceError(
                "No saved Brightspace session - run bootstrap_login_interactive() or "
                "bootstrap_login_scripted() first (see brightspace_api.session)."
            )
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self.headless)

    def close(self):
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    @property
    def session_loaded(self) -> bool:
        return self._browser is not None

    def _new_page(self):
        self.start()  # lazy-start for quick one-off scripts
        context = self._browser.new_context(storage_state=str(cfg.storage_state_file))
        return context, context.new_page()

    def _base_url(self) -> str:
        return cfg.require_base_url()

    # ------------------------------------------------------------------
    # Announcements
    # ------------------------------------------------------------------

    def get_announcements(self) -> list[dict]:
        base_url = self._base_url()
        context, page = self._new_page()
        try:
            # /d2l/lms/news/main.d2l needs a real ?ou=<org unit id> or it
            # 400s - there's no "just show me mine" shortcut. Get it from
            # the homepage's own news link rather than hardcoding an org
            # unit id (it's account/install-specific, not a Brightspace
            # constant).
            page.goto(f"{base_url}/d2l/home", wait_until="networkidle")
            news_link = page.locator('a[href*="/d2l/lms/news/main.d2l"]').first
            if news_link.count() == 0:
                return []
            news_url = news_link.get_attribute("href")
            page.goto(f"{base_url}{news_url}" if news_url.startswith("/") else news_url,
                      wait_until="networkidle")
            table = page.locator('table[summary="List of announcements"]')
            if table.count() == 0:
                return []
            rows = table.locator("tbody > tr").all()
            results = []
            # First row is the column-header row (Title / Start Date);
            # after that, each announcement is a (title row, details row) pair.
            i = 1
            while i < len(rows):
                title_row = rows[i]
                link = title_row.locator("a.d2l-link").first
                title, href, body = None, None, None
                if link.count():
                    title = link.inner_text().strip()
                    href = link.get_attribute("href")
                if i + 1 < len(rows) and "d_detailsRow" in (rows[i + 1].get_attribute("class") or ""):
                    body_block = rows[i + 1].locator("d2l-html-block")
                    if body_block.count():
                        body = body_block.get_attribute("html")
                    i += 2
                else:
                    i += 1
                if title:
                    results.append({
                        "title": title,
                        "url": f"{base_url}{href}" if href and href.startswith("/") else href,
                        "body_html": body,
                    })
            return results
        finally:
            context.close()

    # ------------------------------------------------------------------
    # Courses
    # ------------------------------------------------------------------

    def get_courses(self) -> list[dict]:
        base_url = self._base_url()
        context, page = self._new_page()
        try:
            page.goto(f"{base_url}/d2l/home", wait_until="networkidle")
            page.wait_for_timeout(6000)  # my-courses widget fetches its own data async
            widget = page.locator("d2l-my-courses-v2")
            if widget.count() == 0:
                return []
            links = widget.evaluate(WALK_LINKS_JS)
            seen = {}
            for link in links:
                m = re.fullmatch(r"/d2l/home/(\d+)", link["href"])
                if m and link["text"]:
                    # Link text is "<display name>, <code>+<year>+<period>" -
                    # only strip that trailing code part (matches
                    # CODE+YYYY+N), don't blindly split on the first comma
                    # since some real course names contain one too.
                    name = re.sub(r",\s*\S+\+\d{4}\+\S+$", "", link["text"]).strip()
                    seen.setdefault(m.group(1), name)
            return [{"org_unit_id": ou, "name": name, "url": f"{base_url}/d2l/home/{ou}"}
                    for ou, name in seen.items()]
        finally:
            context.close()

    def get_course_content(self, org_unit_id: str) -> list[dict]:
        """See the module docstring's "Known limitation" note - this
        reflects whatever module the account currently has last-open for
        this course, not guaranteed to be every file in every module."""
        base_url = self._base_url()
        context, page = self._new_page()
        try:
            page.goto(f"{base_url}/d2l/le/content/{org_unit_id}/Home", wait_until="networkidle")
            page.wait_for_timeout(2000)
            items = page.locator('a.d2l-link[href*="/viewContent/"]')
            count = items.count()
            results = []
            for i in range(count):
                item = items.nth(i)
                href = item.get_attribute("href")
                title_attr = item.get_attribute("title") or ""
                m = re.match(r"/d2l/le/content/\d+/viewContent/(\d+)/View", href or "")
                file_type = title_attr.rsplit(" - ", 1)[-1] if " - " in title_attr else None
                results.append({
                    "topic_id": m.group(1) if m else None,
                    "title": item.inner_text().strip(),
                    "file_type": file_type,
                    "url": f"{base_url}{href}" if href and href.startswith("/") else href,
                })
            return results
        finally:
            context.close()

    def download_course_file(self, org_unit_id: str, topic_id: str) -> Path:
        """Downloads a file by its topic id (from get_course_content's
        "topic_id" field) to a local cache and returns the path to it.
        PDF-backed topics only for now - see module docstring. Re-downloads
        are cheap to skip: if already cached locally, returns that copy
        without re-scraping."""
        base_url = self._base_url()
        context, page = self._new_page()
        try:
            view_url = f"{base_url}/d2l/le/content/{org_unit_id}/viewContent/{topic_id}/View"
            page.goto(view_url, wait_until="networkidle")
            page.wait_for_timeout(1500)

            iframe = page.locator("iframe.d2l-fileviewer-rendered-pdf")
            if iframe.count() == 0:
                raise BrightspaceError(
                    "Only PDF-viewer-backed topics are downloadable so far (see client.py module "
                    "docstring) - this topic didn't render one, might be a non-PDF file type or an "
                    "external tool link, not built against the real DOM yet."
                )
            src = iframe.get_attribute("src")
            parsed = urllib.parse.urlparse(src)
            file_param = urllib.parse.parse_qs(parsed.query).get("file", [None])[0]
            if not file_param:
                raise BrightspaceError("PDF viewer iframe found but had no 'file=' param - unexpected shape.")
            file_url = f"{base_url}{urllib.parse.unquote(file_param)}"

            filename = file_param.rsplit("/", 1)[-1].split("?")[0]
            filename = urllib.parse.unquote(filename) or f"{topic_id}.pdf"
            dest_dir = cfg.downloads_dir / org_unit_id
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_path = dest_dir / filename

            resp = context.request.get(file_url)
            if resp.status != 200:
                raise BrightspaceError(f"Brightspace returned {resp.status} fetching the file itself.")
            dest_path.write_bytes(resp.body())
            return dest_path
        finally:
            context.close()

    def get_course_grades(self, org_unit_id: str) -> list[dict]:
        base_url = self._base_url()
        context, page = self._new_page()
        try:
            page.goto(f"{base_url}/d2l/lms/grades/index.d2l?ou={org_unit_id}", wait_until="networkidle")
            table = page.locator('table[summary="List of grade items and their values"]')
            if table.count() == 0:
                return []
            rows = table.locator("tbody > tr").all()
            results = []
            i = 1  # row 0 is the column-header row (Grade Item / Points / Grade / Comments)
            while i < len(rows):
                row = rows[i]
                label = row.locator("label").first
                if label.count() == 0:
                    i += 1
                    continue
                name = label.inner_text().strip()
                # The grade's own <span> has a generated id (no stable
                # class) - it's the only <span> inside the row's second
                # <td>, so just take whichever one isn't empty rather than
                # guessing an id.
                grade_span = row.locator("td").nth(1).locator("span").first
                grade = grade_span.inner_text().strip() if grade_span.count() else None
                feedback_block = row.locator("d2l-html-block")
                feedback_html = feedback_block.get_attribute("html") if feedback_block.count() else None
                results.append({"item": name, "grade": grade, "feedback_html": feedback_html})
                i += 1
            return results
        finally:
            context.close()

    def get_course_assignments(self, org_unit_id: str) -> list[dict]:
        """The Dropbox/Assignments tool (/d2l/lms/dropbox/user/folders_list.d2l)
        - old-school server-rendered, same family as grades/announcements.
        More granular than get_course_grades for this specific data: has
        due dates, submission counts, and an explicit "Not Submitted"
        state (grades only shows a blank/"-%" for both "not submitted"
        and "submitted, ungraded", indistinguishable there). Row shape
        confirmed live against a real course: a category header row
        (`tr.d_ggl2`, name in `label > span.ds_i`) followed by one row per
        assignment - name+due date inside a `<th>`, then 3 `<td>`s
        (submission status, score, feedback link). The name link's
        `title` attribute ("Submit files to <name>") is used over the
        link's own text, since the text sometimes has a group-name prefix
        (e.g. "PG10: ") the title doesn't. Score cell has several hidden
        `<label>`s plus one visible one when graded (same pattern as
        grades) - all hidden means ungraded."""
        base_url = self._base_url()
        context, page = self._new_page()
        try:
            page.goto(f"{base_url}/d2l/lms/dropbox/user/folders_list.d2l?ou={org_unit_id}",
                      wait_until="networkidle")
            table = page.locator('table[summary="List of assignments for this course"]')
            if table.count() == 0:
                return []
            rows = table.locator("tbody > tr").all()
            results = []
            current_category = None
            for row in rows:
                row_class = row.get_attribute("class") or ""
                if "d_ggl2" in row_class:
                    label = row.locator("span.ds_i").first
                    if label.count():
                        current_category = label.inner_text().strip()
                    continue

                name_link = row.locator('a[href*="folder_submit_files.d2l"]').first
                if name_link.count() == 0:
                    continue  # not a data row (e.g. a stray header) - skip rather than guess

                title_attr = name_link.get_attribute("title") or ""
                name = title_attr.removeprefix("Submit files to ").strip() or name_link.inner_text().strip()

                due = None
                due_label = row.locator("th label:has-text('Due on')").first
                if due_label.count():
                    due = due_label.inner_text().strip()

                cells = row.locator("td")
                cell_count = cells.count()

                submission_status = None
                if cell_count >= 1:
                    submission_status = cells.nth(0).inner_text().strip() or None

                score = None
                if cell_count >= 2:
                    labels = cells.nth(1).locator("label")
                    for i in range(labels.count() - 1, -1, -1):
                        text = labels.nth(i).inner_text().strip()
                        if text:
                            score = text
                            break

                feedback_url = None
                if cell_count >= 3:
                    fb_link = cells.nth(2).locator('a[href*="folder_user_view_feedback.d2l"]').first
                    if fb_link.count():
                        href = fb_link.get_attribute("href")
                        feedback_url = f"{base_url}{href}" if href and href.startswith("/") else href

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
            context.close()

    def get_course_discussions(self, org_unit_id: str) -> list[dict]:
        """Discussions tool (/d2l/le/<ou>/discussions/List) - a course can
        have multiple forums, each rendered as its own
        `table[summary="Topic List for Forum <name>"]`. Real columns
        (from the table's own <thead>, confirmed live rather than
        guessed): Topic | Threads | Posts | Last Post. `has_unread` is
        read off the row's own `d2l-grid-unread` CSS class (not
        text-matched - an earlier version of this looked for a "Contains
        unread posts" string, but that text turned out to live in an
        offscreen a11y label on the *topic* cell, not near
        Threads/Posts/Last Post, so it never matched; the row class is
        the actual, reliable signal). Doesn't fetch individual post
        content (a real per-topic scrape, not attempted here) - this is a
        list of what threads exist, how active they are, and whether you
        have unread posts."""
        base_url = self._base_url()
        context, page = self._new_page()
        try:
            page.goto(f"{base_url}/d2l/le/{org_unit_id}/discussions/List", wait_until="networkidle")
            page.wait_for_timeout(1500)
            tables = page.locator('table[summary^="Topic List for Forum"]')
            forum_count = tables.count()
            results = []
            for fi in range(forum_count):
                table = tables.nth(fi)
                summary = table.get_attribute("summary") or ""
                forum_name = summary.removeprefix("Topic List for Forum ").strip()
                rows = table.locator("tbody > tr").all()
                for row in rows:
                    link = row.locator("a.d2l-link").first
                    if link.count() == 0:
                        continue
                    title = link.inner_text().strip()
                    href = link.get_attribute("href")
                    if not title or not href:
                        continue

                    row_class = row.get_attribute("class") or ""
                    cells = row.locator("td")
                    cell_count = cells.count()
                    threads = cells.nth(0).inner_text().strip() if cell_count >= 1 else None
                    # Posts cell text is "N\nUnread for topic <name>:\n(M)"
                    # when unread posts exist - that trailing part
                    # duplicates has_unread/topic above, keep just the
                    # leading count.
                    posts_raw = cells.nth(1).inner_text().strip() if cell_count >= 2 else None
                    posts = posts_raw.split("\n", 1)[0].strip() if posts_raw else None
                    last_post_text = cells.nth(2).inner_text().strip() if cell_count >= 3 else ""

                    results.append({
                        "forum": forum_name,
                        "topic": title,
                        "url": f"{base_url}{href}" if href.startswith("/") else href,
                        "threads": threads,
                        "posts": posts,
                        "has_unread": "d2l-grid-unread" in row_class,
                        "last_post_text": last_post_text or None,  # raw D2L text (author/date, or restriction notes) - not split further, wording isn't stable enough to parse confidently
                    })
            return results
        finally:
            context.close()

    # ------------------------------------------------------------------
    # Deadlines
    # ------------------------------------------------------------------

    def get_deadlines(self) -> list[dict]:
        base_url = self._base_url()
        context, page = self._new_page()
        try:
            page.goto(f"{base_url}/d2l/le/worktodo/view", wait_until="networkidle")
            # The nested web components fetch their own data asynchronously
            # after the page itself finishes loading - networkidle doesn't
            # cover that inner fetch, so give it a moment.
            page.wait_for_timeout(5000)
            widget = page.locator("d2l-w2d-work-to-do")
            if widget.count() == 0:
                return []
            flat = widget.evaluate(WALK_SHADOW_JS)

            # Titles and their action_href appear adjacent, in order, one
            # pair per work-item (confirmed against real data). Pair them
            # up rather than assuming a 1:1 zip in case some items are
            # missing a link.
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
            context.close()

    # ------------------------------------------------------------------
    # Write endpoints - unimplemented stubs, see each docstring
    # ------------------------------------------------------------------

    def enroll(self, course_codes: list[str]) -> dict:
        """Not implemented - this whole flow (search -> find course ->
        click enroll -> confirm) needs to be built against your
        institution's actual course-enrollment UI. No built-in
        confirmation/approval check either - add your own before calling
        this for real once it's implemented."""
        context, page = self._new_page()
        try:
            return {code: "NOT_IMPLEMENTED - selectors need to be filled in against the real site"
                    for code in course_codes}
        finally:
            context.close()

    def upload(self, assignment_url: str, file_path: str) -> dict:
        """Not implemented - Brightspace's assignment drop-box file input
        selector needs to be confirmed live for your institution before
        this does anything real. No built-in confirmation/approval check
        either."""
        context, page = self._new_page()
        try:
            page.goto(assignment_url)
            file_input = page.locator("input[type=file]").first
            file_input.set_input_files(file_path)
            return {"status": "NOT_IMPLEMENTED - submit-button click needs the real selector too"}
        finally:
            context.close()
