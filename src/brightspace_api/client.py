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

2026-08-31 - THE REAL CONTENT API, and everything above about content/
modules/description/download rewritten to use it. Root cause of a real
live break: TU Delft rolled out a newer content UI ("Lessons", built on
a `smart-curriculum` Lit/Polymer web component with real shadow DOM,
different URLs (`/d2l/le/lessons/<ou>/{units,topics}/<id>` instead of
`/d2l/le/content/<ou>/viewContent/<id>/View`) and a different tree
structure entirely (`d2l-list-item-nav`, not `li.d2l-le-TreeAccordionItem`)
- confirmed live this is now the DEFAULT for every current-quarter
course (checked 3/3 of Jan's active 2026/27 Q1 courses, all on the new
UI; only an already-finished past-quarter course still showed the old
tree), so every method built against the old DOM silently broke for
anything Jan is actually taking right now: get_course_content/
get_course_modules/get_module_content/get_all_course_content all
returned empty `[]` (no error - looked exactly like "no content posted
yet"), get_external_link and download_course_file both raised
"not found"-style errors.

While investigating the new UI's shadow DOM (a real generic recursive
shadow-piercing walker was built and worked - see git history if ever
needed again for something this doesn't already cover), found something
much better: Brightspace has a real, clean, officially-versioned REST
API behind BOTH UIs - `GET /d2l/api/le/unstable/<ou>/content/toc?loadDescription=true`
- confirmed live it returns the exact same nested Modules/Topics JSON
tree (real `ModuleId`/`TopicId`/`Title`/`TypeIdentifier`/`Url`/
`Completed`/`Description.Html` fields) for BOTH an old-UI course and a
new-UI course identically - this is the actual shared data source both
frontends render from, not something specific to the redesign. `Url` is
the real destination directly for a `TypeIdentifier: "File"` topic (a
static, session-authenticated `/content/enforced/...` path - no more
scraping a PDF-viewer iframe's `src`) and a real LTI-launch quicklink
for a `TypeIdentifier: "Link"` topic (confirmed live resolving a real
quiz topic through to `ans.app/digital_test/assignments/.../results/new`
with zero extra login - see [[project_personal_agent]] auto-memory for
the Hydromechanica case this was found from).

Net effect: `_get_toc()`/`_flatten_toc()` (new) replace essentially all
of the DOM-walking described above - get_course_content/
get_course_modules/get_module_description/get_module_content/
get_all_course_content are now plain API calls, no browser page
rendering needed at all for any of them (a lightweight
`context.request` via the new `_api_get()` helper, not `_new_page()`).
get_external_link/download_course_file still use a real browser page
for the FINAL hop only (resolving the actual LTI redirect chain / that
one authenticated file GET), now starting from the real URL the TOC API
provides instead of a guessed/broken one. The old dedupe heuristic in
get_module_content (a workaround for the old UI's own inconsistent
click-to-view behavior) is gone entirely - nothing to dedupe against a
real structured API; `dedupe` params on the affected methods are now
no-ops kept only so existing callers don't break on the signature.
`file_type` on every returned item is now Brightspace's own
`TypeIdentifier` (e.g. `"File"`, `"Link"`) - NOT the old anchor-title-
derived string - a real behavior change for any caller matching on the
old values.
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

    def _api_get(self, path: str, params: dict | None = None) -> dict:
        """Plain authenticated GET against Brightspace's own REST API
        (`/d2l/api/...`), using the same session cookies as everything
        else here - no browser page/rendering needed, just a lightweight
        API request context. See module docstring's "The real content
        API" section for how this was found and why it replaced almost
        all of the old DOM-scraping content methods below."""
        self.start()
        base_url = self._base_url()
        context = self._browser.new_context(storage_state=str(cfg.storage_state_file))
        try:
            resp = context.request.get(f"{base_url}{path}", params=params or {})
            if resp.status != 200:
                raise BrightspaceError(f"Brightspace API GET {path} returned {resp.status}: {resp.text()[:300]}")
            return resp.json()
        finally:
            context.close()

    def _get_toc(self, org_unit_id: str) -> dict:
        """Raw table-of-contents JSON for a course - real, official,
        UI-independent Brightspace REST API, NOT scraped HTML. Confirmed
        live it works identically for both the old classic tree UI and
        the newer `smart-curriculum`/Lessons UI (2026-08-31 finding) -
        this is the actual data source both frontends render from, so
        it's the right thing to call regardless of which UI a given
        course happens to use. `loadDescription=true` is required to get
        each Module's/Topic's own `Description.Html` populated (used by
        get_module_description) - without it every Description comes
        back empty."""
        return self._api_get(f"/d2l/api/le/unstable/{org_unit_id}/content/toc", params={"loadDescription": "true"})

    @staticmethod
    def _flatten_toc(modules: list[dict], folder_path: list[str] | None = None) -> list[dict]:
        """Recursively flattens the TOC's nested Modules/Topics tree into
        one flat list of real content items (Topics only - Modules
        themselves are folders, not content, see get_course_modules for
        those). Each item's `folder_path` is the chain of module names
        from the course root down to wherever it actually lives (`[]` for
        a topic directly in a top-level module) - same field/meaning as
        the old DOM-scraped get_module_content used to produce, kept
        for compatibility with existing callers."""
        folder_path = folder_path or []
        out = []
        for module in modules:
            for topic in module.get("Topics", []):
                out.append({
                    "topic_id": str(topic["TopicId"]),
                    "title": topic["Title"],
                    "file_type": topic.get("TypeIdentifier"),
                    "url": topic.get("Url"),
                    "content_page_url": topic.get("ContentUrl"),
                    "completed": topic.get("Completed", False),
                    "folder_path": list(folder_path),
                })
            if module.get("Modules"):
                out.extend(BrightspaceClient._flatten_toc(module["Modules"], folder_path + [module["Title"]]))
        return out

    def get_course_content(self, org_unit_id: str) -> list[dict]:
        """ALL real content items across the WHOLE course - every module,
        however deeply nested. This used to only reflect whichever module
        the account happened to have last open (see git history for the
        old DOM-scraped version and its documented limitation) - fixed
        2026-08-31 by switching to the real TOC API (`_get_toc`), which
        has no such "currently open" concept at all. `file_type` is now
        Brightspace's own `TypeIdentifier` field (e.g. "File", "Link") -
        NOT the old anchor-title-derived string, a real behavior change
        for any existing caller matching on specific old file_type
        values."""
        toc = self._get_toc(org_unit_id)
        return self._flatten_toc(toc.get("Modules", []))

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

        **2026-08-31**: switched from scraping the classic tree HTML to
        the real TOC API (see `_get_toc`) - `include_navigation_tabs` is
        now a no-op kept only for backward call-signature compatibility.
        The TOC API has no concept of the old UI's generic navigational
        tabs ("Overview"/"Bookmarks"/etc, which were never real modules
        anyway, just fixed accordion entries) - they simply don't appear
        in this data at all, so there's nothing left to filter."""
        toc = self._get_toc(org_unit_id)
        return [{"module_id": str(m["ModuleId"]), "name": m["Title"]} for m in toc.get("Modules", [])]

    def get_module_description(self, org_unit_id: str, module_name: str) -> dict | None:
        """A specific top-level module's description/overview text (e.g.
        the weekly "Leerdoelen" blocks TU Delft courses commonly use).
        Matches `module_name` against the module title (case-insensitive
        substring) - use get_course_modules() first if you're not sure of
        the exact name. Returns None if nothing matched.

        **2026-08-31**: switched from click-and-scrape to the real TOC
        API (see `_get_toc`) - each Module object already carries its own
        `Description.Html` when the API is called with
        `loadDescription=true` (which `_get_toc` always does), so no
        clicking or page navigation is needed at all anymore. Old
        docstring's claim that the module tree is "JS-driven, not
        URL-addressable" is still true of the UI, just no longer
        relevant - this doesn't touch the UI."""
        toc = self._get_toc(org_unit_id)
        for module in toc.get("Modules", []):
            if module_name.lower() in module["Title"].lower():
                return {"name": module["Title"], "description_html": module.get("Description", {}).get("Html")}
        return None

    def get_module_content(self, org_unit_id: str, module_name: str, dedupe: bool = True) -> list[dict] | None:
        """Like get_course_content, but scoped to one specific top-level
        module - descends into that module's nested sub-folders however
        deep they go. Each returned item has a `folder_path` field - the
        chain of folder names from the module root down to wherever the
        item actually lives (`[]` for an item directly in the module
        itself).

        **2026-08-31**: switched from click-and-scrape to the real TOC
        API (see `_get_toc`/`_flatten_toc`) - the old docstring's
        documented gaps (get_course_content only seeing "whichever module
        happens to be selected", and folders needing a dedupe heuristic
        because clicking one showed a confusing mix of its own + a
        sub-folder's items) were both artifacts of the old DOM-scraping
        approach and don't exist with a real structured API - there's
        nothing to select, click, or accidentally merge. `dedupe` is now
        a no-op kept only for backward call-signature compatibility.

        Returns None if no module matched module_name."""
        toc = self._get_toc(org_unit_id)
        for module in toc.get("Modules", []):
            if module_name.lower() in module["Title"].lower():
                return self._flatten_toc([module])
        return None

    def get_all_course_content(self, org_unit_id: str, dedupe: bool = True) -> list[dict]:
        """The whole course's content, every module and however deeply
        nested, each item tagged with which top-level `module` it came
        from (in addition to `folder_path`, its location within that
        module).

        **2026-08-31**: now a thin wrapper - get_course_content() itself
        already returns the whole course via the real TOC API (see that
        method's docstring for the history of why this distinction used
        to matter), so this just adds the extra `module` field on top
        rather than doing its own separate module-by-module walk.
        `dedupe` is now a no-op kept only for backward call-signature
        compatibility."""
        toc = self._get_toc(org_unit_id)
        all_items = []
        for module in toc.get("Modules", []):
            for item in self._flatten_toc([module]):
                item["module"] = module["Title"]
                all_items.append(item)
        return all_items

    def get_external_link(self, org_unit_id: str, topic_id: str) -> dict:
        """Resolves the real destination behind a Content topic that's an
        external link/tool (an LTI launch, `TypeIdentifier: "Link"` in
        the TOC data - the kind get_course_content()/get_module_content()
        list with a `file_type` that isn't `"File"`). The destination
        isn't a static URL Brightspace will just hand you - it requires
        actually following the real LTI launch handshake (a signed,
        already-authenticated redirect/form-post chain), so this
        navigates a real browser page through it and reports where it
        lands.

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
        navigation ended up", not a guaranteed final content URL.
        `likely_requires_separate_login` is a best-effort heuristic
        (checked against the real TU Delft/SURFconext login page from the
        second case above, but not against a broad sample of other
        institutions' SSO pages) - True if the landing URL/title matches
        common login-page patterns (a `login.`/`sso.`/`auth.` subdomain,
        or "log in"/"sign in"/"inloggen" in the title), False otherwise.
        It's a hint to check manually, not a guarantee either way.

        **2026-08-31**: the old "Open in New Window" button-click
        approach broke outright on TU Delft's newer Lessons/
        `smart-curriculum` UI (different button, "Open Link", and the
        classic `/viewContent/{id}/View` URL this used to start from
        doesn't correspond to anything in the new UI either). Fixed by
        looking the topic up in the real TOC API first (see `_get_toc`)
        to get its actual `Url` (a `quickLink.d2l?...type=lti...`
        launcher) and navigating there directly - UI-independent, and
        more direct than clicking a button whose exact label/selector
        can change again. Raises if the topic isn't found or isn't a
        Link-type topic (a File-type topic has no "external destination"
        to resolve - use download_course_file instead)."""
        toc = self._get_toc(org_unit_id)
        topic = next((t for t in self._flatten_toc(toc.get("Modules", [])) if t["topic_id"] == str(topic_id)), None)
        if topic is None:
            raise BrightspaceError(f"Topic {topic_id!r} not found in this course's TOC.")
        if topic["file_type"] == "File":
            raise BrightspaceError(
                f"Topic {topic_id!r} ({topic['title']!r}) is a File, not an external link - "
                "use download_course_file instead."
            )
        if not topic.get("url"):
            raise BrightspaceError(f"Topic {topic_id!r} ({topic['title']!r}) has no Url in the TOC data to follow.")

        base_url = self._base_url()
        context, page = self._new_page()
        try:
            page.goto(f"{base_url}{topic['url']}", wait_until="networkidle")
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass  # some destinations keep a long-lived connection open (streaming, polling) - report wherever it got to rather than hang
            landed_url, landed_title = page.url, page.title()
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
        Re-downloads are cheap to skip: if already cached locally,
        returns that copy without re-fetching.

        **2026-08-31**: switched from scraping the PDF-viewer iframe's
        `src` (broken outright on the new Lessons UI - it uses a
        different `d2l-pdf-viewer` custom element with a shadow-DOM
        canvas render, no plain iframe at all) to the real TOC API,
        which already has the actual static file URL in the topic's
        `Url` field (e.g. `/content/enforced/844747-.../Hydromechanics.pdf`)
        - no page rendering needed at all, just fetch that URL directly.
        Only File-type topics have a real file to fetch this way; raises
        for anything else (same as before, just a clearer check)."""
        toc = self._get_toc(org_unit_id)
        topic = next((t for t in self._flatten_toc(toc.get("Modules", [])) if t["topic_id"] == str(topic_id)), None)
        if topic is None:
            raise BrightspaceError(f"Topic {topic_id!r} not found in this course's TOC.")
        if topic["file_type"] != "File" or not topic.get("url"):
            raise BrightspaceError(
                f"Topic {topic_id!r} ({topic['title']!r}) isn't a downloadable File "
                f"(TypeIdentifier={topic['file_type']!r}) - use get_external_link for a Link-type topic."
            )

        base_url = self._base_url()
        file_url = f"{base_url}{topic['url']}"
        filename = urllib.parse.unquote(topic["url"].rsplit("/", 1)[-1].split("?")[0]) or f"{topic_id}.pdf"
        dest_dir = cfg.downloads_dir / org_unit_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / filename

        self.start()
        context = self._browser.new_context(storage_state=str(cfg.storage_state_file))
        try:
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
