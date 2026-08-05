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

2026-08-05, added get_course_modules/get_module_description,
get_notifications, and a real upload(). See each method's own docstring
for the full detail. Short version:
- Module description text (e.g. a course's weekly "Leerdoelen" blocks)
  lives on a JS-driven tree with no per-module URL - loading a specific
  module's text means actually clicking it (confirmed live), there's no
  link to construct directly.
- The homepage has TWO different bell icons ("Subscription alerts" and
  "Update alerts", confirmed live via their distinct aria-labels) -
  get_notifications() is the second one, the general cross-course
  activity feed. Only its first page (5 items) is read; older items sit
  behind a real "Load More" pager, not walked here.
- upload() is real (confirmed live end-to-end through staging a file),
  but its final step - actually clicking Submit - was deliberately never
  exercised against a live assignment (real, hard-to-reverse academic
  action) and defaults off (`confirm_submit=False`). See upload()'s own
  docstring before turning that on for real.

2026-08-05, later same day, added get_module_content() and
get_external_link() (found via a real Jan screenshot of an "External
Resource" topic he couldn't get data for). Short version, see each
method's own docstring for the full detail:
- get_course_content() has a real gap: a module's content isn't
  necessarily flat, and it only ever sees whichever single folder
  happens to already be selected. Confirmed live 3 levels deep on a real
  course (Week module -> a lecture sub-folder -> a further "Werkcollege"
  sub-folder). get_module_content() walks a module's whole subtree
  instead - needed real DOM investigation to get right (direct-child-only
  tree selection, `:scope > ul > li...`, not "all descendants", to avoid
  double-visiting deeper folders).
- get_external_link() resolves an External Resource/External Learning
  Tool topic's "Open in New Window" destination by actually clicking it -
  confirmed live it sometimes resolves cleanly (an LTI-launched tool,
  already authenticated via the existing session) and sometimes lands on
  a separate service's own login page instead (a plain external link
  needing its own auth) - this can't distinguish the two automatically.
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


# Best-effort patterns for "did this navigation land on a login page" -
# see get_external_link's docstring for what this is and isn't good for.
# Deliberately broad/generic (subdomain conventions + common
# EN/NL login wording) rather than TU-Delft-specific, but only ever
# verified against one real institution's SSO page - treat matches as a
# hint, not a certainty.
_LOGIN_URL_HINTS = ("login.", "sso.", "auth.", "/login", "/sso/", "/signin", "/sign-in")
_LOGIN_TITLE_HINTS = ("log in", "login", "sign in", "inloggen")


def _looks_like_login_page(url: str, title: str) -> bool:
    url_l, title_l = (url or "").lower(), (title or "").lower()
    return any(h in url_l for h in _LOGIN_URL_HINTS) or any(h in title_l for h in _LOGIN_TITLE_HINTS)


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

    def get_course_modules(self, org_unit_id: str, include_navigation_tabs: bool = False) -> list[dict]:
        """Lists the top-level module tree entries in a course's Content
        area (id + name) - e.g. "Week 1: Spanning en Rek". Use `name`
        with get_module_description() or get_module_content() to load
        that specific module's own description text or full subtree.
        Only lists TOP-level modules (a module nested inside another
        doesn't get its own entry here) - get_module_content() descends
        into a given module's own nested sub-folders, but this listing
        itself stays flat. The tree itself is JS-driven (see
        get_module_description's docstring for why that matters), but
        listing the top-level names doesn't require clicking anything -
        only loading one specific module's content/description does.

        By default, excludes a handful of fixed navigational tabs that
        live in the same tree as real modules but aren't actual content
        (confirmed live: "Overview", "Bookmarks", "Course Schedule",
        "Table of Contents" - always present, generic across TU Delft
        courses, never had a `module_id`). Pass
        `include_navigation_tabs=True` to get the old, unfiltered
        behavior back (those items are still returned with
        `module_id: None`, same as before)."""
        base_url = self._base_url()
        context, page = self._new_page()
        try:
            page.goto(f"{base_url}/d2l/le/content/{org_unit_id}/Home", wait_until="networkidle")
            page.wait_for_timeout(2000)
            items = page.locator("li.d2l-le-TreeAccordionItem-Root")
            results = []
            for i in range(items.count()):
                item = items.nth(i)
                data_key = item.get_attribute("data-key") or ""
                m = re.search(r"ModuleCO-(\d+)", data_key)
                module_id = m.group(1) if m else None
                if module_id is None and not include_navigation_tabs:
                    continue
                anchor = item.locator("a.d2l-le-TreeAccordionItem-anchor").first
                # The anchor's inner_text() includes offscreen a11y text
                # after the visible name ("Week 1: Spanning en Rek\n module:
                # contains 2 sub-modules\nselected") - the visible name is
                # always first in DOM order, so the first line is enough,
                # same pragmatic split-on-newline approach used for
                # get_course_discussions' posts count.
                name = anchor.inner_text().split("\n")[0].strip() if anchor.count() else None
                if name:
                    results.append({"module_id": module_id, "name": name})
            return results
        finally:
            context.close()

    def get_module_description(self, org_unit_id: str, module_name: str) -> dict | None:
        """Loads a specific top-level module's description/overview text
        (e.g. the weekly "Leerdoelen" blocks TU Delft courses commonly
        use) from the Content area. Matches `module_name` against the
        visible tree item text (case-insensitive substring) - use
        get_course_modules() first if you're not sure of the exact name.
        Returns None if nothing matched.

        The module tree is JS-driven, not URL-addressable: each item's
        link is `href="javascript:void(0)"`, triggering an in-page AJAX
        update that swaps the right-hand panel without changing the URL
        (confirmed live) - so there's no direct link to construct for "the
        page for module X", the only way to load a specific module's text
        is to actually click it, which is what this does.

        Description is returned as raw HTML (`description_html`, same
        `d2l-html-block` pattern used for announcements/grades feedback
        elsewhere) - it's whatever rich text the instructor put there,
        not plain text."""
        base_url = self._base_url()
        context, page = self._new_page()
        try:
            page.goto(f"{base_url}/d2l/le/content/{org_unit_id}/Home", wait_until="networkidle")
            page.wait_for_timeout(2000)
            items = page.locator("li.d2l-le-TreeAccordionItem-Root")
            target_anchor, matched_name = None, None
            for i in range(items.count()):
                item = items.nth(i)
                anchor = item.locator("a.d2l-le-TreeAccordionItem-anchor").first
                if anchor.count() == 0:
                    continue
                name = anchor.inner_text().split("\n")[0].strip()
                if module_name.lower() in name.lower():
                    target_anchor, matched_name = anchor, name
                    break
            if target_anchor is None:
                return None

            target_anchor.click()
            page.wait_for_timeout(2000)
            block = page.locator("div.d2l-htmlblock-untrusted d2l-html-block").first
            description_html = block.get_attribute("html") if block.count() else None
            return {"name": matched_name, "description_html": description_html}
        finally:
            context.close()

    def get_module_content(self, org_unit_id: str, module_name: str, dedupe: bool = True) -> list[dict] | None:
        """Like get_course_content, but scoped to one specific top-level
        module (matched the same way as get_module_description - see its
        docstring for why clicking is unavoidable here) and - unlike
        get_course_content - actually descends into that module's nested
        sub-folders instead of only seeing whichever single folder
        happens to be selected.

        This exists because get_course_content has a real, confirmed-live
        gap: a module's content isn't necessarily flat. Real example:
        Sterkteleer's "Week 1: Spanning en Rek" module contains a
        sub-folder "College 1 Spanning", which itself contains a further
        sub-folder "Werkcollege 1" - three levels deep before reaching
        actual files. get_course_content() only ever sees whichever ONE
        of these happens to be the currently-selected view; it has no way
        to know the others exist. This method walks the whole subtree
        under module_name instead, however deep it goes (bounded by
        max_depth as a safety cap - not because deeper nesting was
        observed, just as a defensive limit against an unexpected
        circular/runaway tree).

        Each returned item has a `folder_path` field - a list of folder
        names from the module root down to wherever the item actually
        lives (e.g. `["College 1 Spanning", "Werkcollege 1"]`, or `[]` for
        an item directly in the module itself) - so results from
        different folders aren't ambiguous.

        Direct-child selection (not "all descendants") is the whole
        reason this needed real DOM investigation rather than reusing
        get_course_content's simpler query: a naive
        `tree_item.locator("li.d2l-le-TreeAccordionItem")` matches EVERY
        descendant at any depth, not just immediate children, which would
        silently double-visit deeper folders once as a false direct child
        and again during real recursion. Confirmed live that
        `:scope > ul > li.d2l-le-TreeAccordionItem` isolates exactly one
        level at a time instead.

        **dedupe=True (the default)**: a folder that has sub-folders
        doesn't reliably show a distinct "just this folder's own items"
        view when clicked - confirmed live it can instead show a mix that
        includes some or all of a sub-folder's own items too (e.g.
        Sterkteleer's "Week 1" module showed exactly its first
        sub-folder's content; "College 1 Spanning" showed its 3 own items
        PLUS all 3 of its "Werkcollege 1" sub-folder's items merged
        together). Root cause not fully pinned down (could be a stale
        selection carried over from the click sequence, could be D2L
        deliberately flattening nested content into parent views) - but
        the *pattern* is consistent and testable: when the exact same
        topic_id shows up at more than one depth within the same branch,
        it's always at a shallower folder_path AND a deeper one together,
        never at two unrelated branches. With dedupe on, this keeps only
        the deepest (most specific) occurrence of each topic_id and drops
        the shallower one(s) - a heuristic, not a proven-correct
        interpretation of which occurrence is the "real" one, but it
        turns a confusing near-duplicate list into a clean one for the
        common case. Pass dedupe=False to get the complete, unfiltered
        data instead (useful if you want to inspect the raw overlap
        yourself, or if your course structure doesn't match the pattern
        above).

        Returns None if no module matched module_name."""
        base_url = self._base_url()
        context, page = self._new_page()
        try:
            page.goto(f"{base_url}/d2l/le/content/{org_unit_id}/Home", wait_until="networkidle")
            page.wait_for_timeout(2000)
            items = page.locator("li.d2l-le-TreeAccordionItem-Root")
            target_item = None
            for i in range(items.count()):
                item = items.nth(i)
                anchor = item.locator("a.d2l-le-TreeAccordionItem-anchor").first
                if anchor.count() == 0:
                    continue
                name = anchor.inner_text().split("\n")[0].strip()
                if module_name.lower() in name.lower():
                    target_item = item
                    break
            if target_item is None:
                return None

            results = []
            self._collect_content_items(page, base_url, target_item, folder_path=[], results=results, depth=0)
            if dedupe:
                results = self._dedupe_by_deepest_folder(results)
            return results
        finally:
            context.close()

    @staticmethod
    def _dedupe_by_deepest_folder(items: list[dict]) -> list[dict]:
        """For each topic_id, keeps only the occurrence(s) at the
        greatest folder_path depth, dropping shallower duplicates. If
        multiple occurrences tie for the deepest depth (genuinely
        different branches, not the same ancestor/descendant chain),
        all of them are kept - this only removes shallower entries that
        have a same-topic_id match somewhere deeper, it never removes
        the only copy of anything. See get_module_content's own
        docstring for why this heuristic exists and its limits."""
        by_topic: dict[str, list[dict]] = {}
        for item in items:
            key = item.get("topic_id") or f"__no_id__{id(item)}"  # items without a topic_id are never deduped against each other
            by_topic.setdefault(key, []).append(item)

        deduped = []
        for key, group in by_topic.items():
            if key.startswith("__no_id__") or len(group) == 1:
                deduped.extend(group)
                continue
            max_depth = max(len(g["folder_path"]) for g in group)
            deduped.extend(g for g in group if len(g["folder_path"]) == max_depth)
        return deduped

    def get_all_course_content(self, org_unit_id: str, dedupe: bool = True) -> list[dict]:
        """The real fix for get_course_content's documented "only sees
        whichever module happens to be selected" limitation: walks EVERY
        top-level module (via get_course_modules - navigation tabs like
        "Bookmarks" excluded by that method's own default) and each
        module's full nested subtree (via get_module_content), and
        aggregates everything into one flat list. This is a genuine
        "show me the entire course" call, not a workaround.

        The real cost: one full page load + click sequence per
        module/sub-folder in the whole course, not the single page load
        get_course_content() uses - for a course with many modules and
        deep nesting this can take a while (tens of seconds to a few
        minutes, roughly linear in the number of modules/folders). Use
        get_module_content() directly instead if you already know which
        module you need - this is for when you genuinely need the whole
        course.

        Each item gets a `module` field (which top-level module it came
        from) in addition to `folder_path` (its location within that
        module - see get_module_content's docstring). `dedupe` is passed
        straight through to each get_module_content() call - see that
        method's docstring for what it does and why it defaults on."""
        modules = self.get_course_modules(org_unit_id)
        all_items = []
        for module in modules:
            items = self.get_module_content(org_unit_id, module["name"], dedupe=dedupe)
            if items is None:
                continue
            for item in items:
                item["module"] = module["name"]
                all_items.append(item)
        return all_items

    def _collect_content_items(self, page, base_url, tree_item, folder_path, results, depth, max_depth=6):
        anchor = tree_item.locator("a.d2l-le-TreeAccordionItem-anchor").first
        anchor.click()
        page.wait_for_timeout(1500)

        file_items = page.locator('a.d2l-link[href*="/viewContent/"]')
        for i in range(file_items.count()):
            it = file_items.nth(i)
            href = it.get_attribute("href")
            title_attr = it.get_attribute("title") or ""
            m = re.match(r"/d2l/le/content/\d+/viewContent/(\d+)/View", href or "")
            file_type = title_attr.rsplit(" - ", 1)[-1] if " - " in title_attr else None
            results.append({
                "topic_id": m.group(1) if m else None,
                "title": it.inner_text().strip(),
                "file_type": file_type,
                "url": f"{base_url}{href}" if href and href.startswith("/") else href,
                "folder_path": list(folder_path),
            })

        if depth >= max_depth:
            return
        sub_items = tree_item.locator(":scope > ul > li.d2l-le-TreeAccordionItem")
        for i in range(sub_items.count()):
            sub = sub_items.nth(i)
            sub_anchor = sub.locator("a.d2l-le-TreeAccordionItem-anchor").first
            if sub_anchor.count() == 0:
                continue
            sub_name = sub_anchor.inner_text().split("\n")[0].strip()
            self._collect_content_items(
                page, base_url, sub, folder_path=folder_path + [sub_name], results=results, depth=depth + 1
            )

    def get_external_link(self, org_unit_id: str, topic_id: str) -> dict:
        """Resolves the real destination behind a Content topic's "Open
        in New Window" button (External Resource / External Learning
        Tool topics - the kind get_course_content()/get_module_content()
        list with a `file_type` that isn't a plain file). The button's
        destination isn't a static href in the page - Brightspace
        resolves it via JS at click time (often an LTI launch through
        `/d2l/lti/...` that hands off a signed, already-authenticated
        request) - so this actually clicks it and reports where the
        resulting new tab lands.

        Confirmed live against two real, different cases:
        - An "External Learning Tool" (LTI) topic resolved cleanly all
          the way through to the real destination (ans.app, a quiz
          platform) with no extra login needed - the LTI handshake
          authenticates it directly using the existing Brightspace
          session.
        - A plain "External Resource" topic (a lecture-recording link)
          did NOT resolve all the way - it landed on a completely
          separate service's own SSO login page instead, because that
          particular external tool needs its own authentication beyond
          whatever the Brightspace session covers.

        Because of that second case, treat `url`/`title` as "wherever the
        button's own navigation ended up", not a guaranteed final content
        URL. `likely_requires_separate_login` is a best-effort heuristic
        (checked against the real TU Delft/SURFconext login page from the
        second case above, but not against a broad sample of other
        institutions' SSO pages) - True if the landing URL/title matches
        common login-page patterns (a `login.`/`sso.`/`auth.` subdomain,
        or "log in"/"sign in"/"inloggen" in the title), False otherwise.
        It's a hint to check manually, not a guarantee either way - a
        genuine content page could coincidentally match, and an
        unfamiliar institution's login page might not match any of these
        patterns at all."""
        base_url = self._base_url()
        context, page = self._new_page()
        try:
            view_url = f"{base_url}/d2l/le/content/{org_unit_id}/viewContent/{topic_id}/View"
            page.goto(view_url, wait_until="networkidle")
            page.wait_for_timeout(1500)

            btn = page.locator('button:has-text("Open in New Window")').first
            if btn.count() == 0:
                raise BrightspaceError(
                    "No 'Open in New Window' button found - this topic might be a plain file "
                    "(use download_course_file instead) or a type not seen yet."
                )
            with page.context.expect_page() as new_page_info:
                btn.click()
            new_page = new_page_info.value
            try:
                new_page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass  # some destinations keep a long-lived connection open (streaming, polling) - report wherever it got to rather than hang
            landed_url, landed_title = new_page.url, new_page.title()
            new_page.close()
            return {
                "url": landed_url,
                "title": landed_title,
                "likely_requires_separate_login": _looks_like_login_page(landed_url, landed_title),
            }
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
    # Notifications
    # ------------------------------------------------------------------

    def get_notifications(self) -> list[dict]:
        """The "Update alerts" bell tray on the homepage - a cross-course
        feed of announcements/grade updates/etc (confirmed live:
        "Announcement Posted", "Grade Updated" including the actual new
        grade value in the title, e.g. "...Your grade is: 9,8").

        There are actually TWO bell icons in the D2L top nav, confirmed
        live by their distinct aria-labels - "Subscription alerts" (forum
        subscription notifications, was empty when checked) and "Update
        alerts" (this one, the general activity feed - matches what's
        commonly just called "the notification bell"). Don't assume
        `[aria-label*="Alert"]` uniquely identifies the right one; this
        uses the exact `[aria-label="Update alerts"]` for that reason.

        Backed by a real endpoint
        (/d2l/NavigationArea/<id>/ActivityFeed/GetAlertsDaylight?Category=1)
        but its response is D2L's own RPC-ish "while(1);{...}" format with
        HTML embedded as an escaped string inside JSON, not meaningfully
        simpler to parse than just clicking the bell and reading the
        resulting DOM - so this does the latter. Each item comes from a
        `<li class="d2l-datalist-item">`: title + link from
        `a.d2l-datalist-item-actioncontrol`, type+course from a
        `span.d2l-textblock-secondary` (e.g. "Grade Updated - WBMT1051
        Wiskunde 2 (2025/26 Q3)" - split on the first " - ", since the
        type values are a small fixed vocabulary that never contains one
        itself), and the precise timestamp from the date element's own
        `title` attribute ("Received on Friday, 31 July, 2026 12:07 CET" -
        more precise than the shortened visible text like "31 July").

        **Only returns the first page** (5 items, confirmed live) - there
        is a real "Load More" pager for older items, not walked here.
        This covers "what's new" rather than full notification history."""
        base_url = self._base_url()
        context, page = self._new_page()
        try:
            page.goto(f"{base_url}/d2l/home", wait_until="networkidle")
            page.wait_for_timeout(2000)
            bell = page.locator('[aria-label="Update alerts"]').first
            if bell.count() == 0:
                return []
            bell.click()
            page.wait_for_timeout(2500)

            items = page.locator("li.d2l-datalist-item")
            results = []
            for i in range(items.count()):
                item = items.nth(i)
                link = item.locator("a.d2l-datalist-item-actioncontrol").first
                if link.count() == 0:
                    continue
                title = link.inner_text().strip()
                href = link.get_attribute("href")

                type_course_el = item.locator("span.d2l-textblock-secondary").first
                type_course_text = type_course_el.inner_text().strip() if type_course_el.count() else ""
                kind, _, course = type_course_text.partition(" - ")

                date_el = item.locator(".d2l-navigation-area-activity-message-date").first
                received = None
                if date_el.count():
                    raw = date_el.get_attribute("title") or ""
                    received = raw.removeprefix("Received on ").strip() or None

                results.append({
                    "type": kind.strip() or None,
                    "course": course.strip() or None,
                    "title": title,
                    "url": f"{base_url}{href}" if href and href.startswith("/") else href,
                    "received": received,
                })
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

    def upload(self, assignment_url: str, file_paths: list[str] | str, confirm_submit: bool = False) -> dict:
        """Submits file(s) to a Dropbox/Assignment folder. `assignment_url`
        is the `folder_submit_files.d2l` URL from get_course_assignments'
        `feedback_url`-style link (same URL D2L uses whether or not
        something's already been submitted - it calls this "Submit
        Assignment" regardless).

        Real DOM flow, confirmed LIVE end-to-end (real file, real
        TU Delft dropbox) up through staging a file - see the
        confirm_submit note below for the one part that's NOT been tested
        live:
          1. Click the "Add a File" button.
          2. A file-picker opens in an iframe
             (`/d2l/common/dialogs/file/main.d2l`) - click its "My
             Computer" tab (`li:has(a[title="My Computer"])`; the tab's
             own link text is an offscreen a11y span, not directly
             clickable, so this targets the visible list item instead).
          3. Click "Upload" inside that dialog while Playwright's
             file-chooser listener is armed
             (`page.expect_file_chooser()`), then hand it the real local
             file path - this is a genuine native OS file dialog under a
             real (even if headless) browser, `expect_file_chooser` is
             the correct way to drive that without a display.
          4. Once the upload completes, click "Add" on the OUTER dialog
             wrapper (a *different* frame than steps 2-3 - this button
             lives on the main page, not inside the file-picker iframe)
             to confirm the selection and close the dialog. At this
             point the file is staged in the page's own "Files to
             submit" list - confirmed live that nothing is actually sent
             to the course yet.
          5. Repeat 1-4 for each path in file_paths.
          6. Only if confirm_submit=True: click the real "Submit" button.

        **confirm_submit defaults to False on purpose, and step 6 itself
        has never been run live** - clicking Submit is a real,
        essentially irreversible academic action (a real submission
        attempt, a real timestamp, can affect grading on a real
        assignment), so this was deliberately not exercised against a
        live TU Delft assignment while building it. Steps 1-5 (getting a
        real file staged and ready) ARE confirmed live. Test confirm_submit=True
        yourself on something low-stakes before trusting it blindly - if
        the Submit button's post-click behavior turns out to need
        different handling (e.g. a real page navigation vs. an in-place
        AJAX update - unknown which, see the plain `wait_for_timeout`
        below rather than an assumed `expect_navigation`), that's exactly
        the kind of thing only a real test will surface.

        Returns {"staged_files": [...], "submitted": bool}."""
        context, page = self._new_page()
        try:
            if isinstance(file_paths, str):
                file_paths = [file_paths]

            page.goto(assignment_url, wait_until="networkidle")
            page.wait_for_timeout(1500)

            for path in file_paths:
                add_file_btn = page.locator('button:has-text("Add a File")').first
                if add_file_btn.count() == 0:
                    raise BrightspaceError(
                        "No 'Add a File' button found - assignment_url doesn't look like a real "
                        "folder_submit_files.d2l page, or the DOM has changed since this was built."
                    )
                add_file_btn.click()
                page.wait_for_timeout(2500)

                dialog_frame = next((f for f in page.frames if "dialogs/file" in f.url), None)
                if dialog_frame is None:
                    raise BrightspaceError("File-picker dialog iframe didn't appear after clicking 'Add a File'.")

                my_computer = dialog_frame.locator('li:has(a[title="My Computer"])').first
                if my_computer.count() == 0:
                    raise BrightspaceError("'My Computer' tab not found in the file-picker dialog - unexpected shape.")
                my_computer.click()
                page.wait_for_timeout(2000)
                # the frame object above is now stale (its URL changed when
                # the tab was clicked) - re-fetch it fresh
                dialog_frame = next((f for f in page.frames if "dialogs/file" in f.url), None)

                upload_btn = dialog_frame.locator('button:has-text("Upload")').first
                if upload_btn.count() == 0:
                    raise BrightspaceError("'Upload' button not found in the 'My Computer' panel - unexpected shape.")
                with page.expect_file_chooser() as fc_info:
                    upload_btn.click()
                fc_info.value.set_files(path)
                page.wait_for_timeout(3000)  # real upload transfer, not instant

                confirm_add = page.locator(
                    "d2l-dialog button:has-text('Add'), .d2l-dialog button:has-text('Add'), "
                    "[role=dialog] button:has-text('Add')"
                ).first
                if confirm_add.count() == 0:
                    raise BrightspaceError(
                        "Outer dialog's 'Add' confirm button not found after upload - file may not be "
                        "staged. Don't assume it worked."
                    )
                confirm_add.click()
                page.wait_for_timeout(2000)

            submitted = False
            if confirm_submit:
                submit_btn = page.locator('button:has-text("Submit")').first
                if submit_btn.count() == 0:
                    raise BrightspaceError("No 'Submit' button found after staging files - unexpected page state.")
                submit_btn.click()
                page.wait_for_timeout(3000)
                submitted = True

            return {"staged_files": file_paths, "submitted": submitted}
        finally:
            context.close()
