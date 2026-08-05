"""Optional HTTP wrapper around BrightspaceClient - run this if you want
a long-lived local service other processes/languages can hit over HTTP,
instead of importing brightspace_api directly as a library.

    pip install "brightspace-api[server]"
    brightspace-serve
    # or: uvicorn brightspace_api.server:app --host 127.0.0.1 --port 8000

Every route here is a thin wrapper around the identically-named
BrightspaceClient method (see client.py for what each one actually
scrapes and why) - this module's only real job is the HTTP plumbing.

Threading note: BrightspaceClient uses Playwright's *synchronous* API,
which - unlike the async API - must always be driven from the one OS
thread it was started on. A plain `async def` FastAPI route calling into
it directly would violate that (Starlette would happily call it from
whatever thread), so every route here goes through `_run()`, which
dispatches the actual call onto one dedicated single-worker thread pool
instead of Starlette's general one. This keeps every Playwright call on
the same thread throughout the server's life while the `await` still
lets the asyncio event loop keep serving other things (like /healthz)
while a scrape is in flight.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .client import BrightspaceClient, BrightspaceError
from .session import has_saved_session

# Exactly one worker - Playwright's sync API must always run on the same
# OS thread it was started on, so this also naturally serializes scrapes
# (matches how the underlying browser handles one interaction at a time
# anyway).
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="brightspace-playwright")
_client: BrightspaceClient | None = None


def _start_client_sync():
    global _client
    if has_saved_session():
        c = BrightspaceClient(headless=True)
        c.start()
        _client = c
    else:
        _client = None
        print("[brightspace_api.server] WARNING: no saved session - run `brightspace-login` or "
              "`brightspace-login-scripted` first. Endpoints will 503 until then.")


def _stop_client_sync():
    global _client
    if _client is not None:
        _client.close()
        _client = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(_executor, _start_client_sync)
    yield
    await loop.run_in_executor(_executor, _stop_client_sync)


app = FastAPI(title="brightspace-api", lifespan=lifespan)


async def _run(method, *args):
    """Runs an *unbound* BrightspaceClient method (e.g.
    `BrightspaceClient.get_courses`, not `_client.get_courses`) against
    the live client, on the dedicated Playwright thread. Takes the
    unbound method rather than a bound one specifically so a None client
    gets caught as a clean 503 here, instead of an AttributeError from
    resolving `_client.get_courses` before this function even runs."""
    if _client is None:
        raise HTTPException(503, "No Brightspace session - run `brightspace-login` first.")
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(_executor, method, _client, *args)
    except BrightspaceError as e:
        raise HTTPException(500, str(e))


@app.get("/api/announcements")
async def get_announcements():
    return await _run(BrightspaceClient.get_announcements)


@app.get("/api/courses")
async def get_courses():
    return await _run(BrightspaceClient.get_courses)


@app.get("/api/courses/{org_unit_id}/content")
async def get_course_content(org_unit_id: str):
    return await _run(BrightspaceClient.get_course_content, org_unit_id)


@app.get("/api/courses/{org_unit_id}/download/{topic_id}")
async def download_course_file(org_unit_id: str, topic_id: str):
    path = await _run(BrightspaceClient.download_course_file, org_unit_id, topic_id)
    return FileResponse(str(path), filename=path.name, media_type="application/pdf")


@app.get("/api/courses/{org_unit_id}/modules")
async def get_course_modules(org_unit_id: str):
    return await _run(BrightspaceClient.get_course_modules, org_unit_id)


@app.get("/api/courses/{org_unit_id}/modules/description")
async def get_module_description(org_unit_id: str, name: str):
    """`name` as a query param (not a path segment) since module names
    routinely contain slashes/colons/etc that don't belong in a URL path."""
    result = await _run(BrightspaceClient.get_module_description, org_unit_id, name)
    if result is None:
        raise HTTPException(404, f"No module matching {name!r} found in this course's content tree.")
    return result


@app.get("/api/courses/{org_unit_id}/modules/content")
async def get_module_content(org_unit_id: str, name: str):
    """`name` as a query param, same reason as modules/description above."""
    result = await _run(BrightspaceClient.get_module_content, org_unit_id, name)
    if result is None:
        raise HTTPException(404, f"No module matching {name!r} found in this course's content tree.")
    return result


@app.get("/api/courses/{org_unit_id}/content/{topic_id}/external-link")
async def get_external_link(org_unit_id: str, topic_id: str):
    return await _run(BrightspaceClient.get_external_link, org_unit_id, topic_id)


@app.get("/api/notifications")
async def get_notifications():
    return await _run(BrightspaceClient.get_notifications)


@app.get("/api/courses/{org_unit_id}/grades")
async def get_course_grades(org_unit_id: str):
    return await _run(BrightspaceClient.get_course_grades, org_unit_id)


@app.get("/api/courses/{org_unit_id}/assignments")
async def get_course_assignments(org_unit_id: str):
    return await _run(BrightspaceClient.get_course_assignments, org_unit_id)


@app.get("/api/courses/{org_unit_id}/discussions")
async def get_course_discussions(org_unit_id: str):
    return await _run(BrightspaceClient.get_course_discussions, org_unit_id)


@app.get("/api/deadlines")
async def get_deadlines():
    return await _run(BrightspaceClient.get_deadlines)


class EnrollRequest(BaseModel):
    course_codes: list[str]


@app.post("/api/enroll")
async def enroll(req: EnrollRequest):
    """Write endpoint - BrightspaceClient.enroll does NOT check any kind
    of approval/confirmation itself, so don't expose this port beyond
    localhost, and put your own confirmation step in front of it."""
    return await _run(BrightspaceClient.enroll, req.course_codes)


class UploadRequest(BaseModel):
    assignment_url: str
    file_paths: list[str]
    confirm_submit: bool = False


@app.post("/api/upload")
async def upload(req: UploadRequest):
    """confirm_submit defaults to False - see BrightspaceClient.upload's
    docstring for exactly what that does and doesn't do, and why. Same
    "no built-in approval check" note as /api/enroll also applies: this
    endpoint itself doesn't ask for confirmation beyond that flag, so
    don't expose this port beyond localhost."""
    return await _run(BrightspaceClient.upload, req.assignment_url, req.file_paths, req.confirm_submit)


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "session_loaded": _client is not None}


def main():
    """Entry point for the `brightspace-serve` console script."""
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
