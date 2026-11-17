"""Local HTTP server: a JSON view of the ledger and background jobs (wave, review, commit).

The server is a thin layer over the same functions the CLI uses. It listens on loopback
only and every request needs the bearer token, because a page in any browser tab can reach
`localhost`. The read routes never write; a job can start agents, so the token is the key to
that. Job state lives in memory and the ledger stays the source of truth.
"""

import json
import logging
import re
import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from agent_harness.check import TASKS_DIR
from agent_harness.check import check as check_ledger
from agent_harness.evidence import _load_task
from agent_harness.filesystem import HarnessError, managed_path, project_root
from agent_harness.jobs import Busy, JobManager, JobParams, NotRunning
from agent_harness.models import Task
from agent_harness.schedule import plan

LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
MIN_TOKEN = 16
MAX_BODY = 10_000
MAX_ID_DIGITS = 18
GRACEFUL_SHUTDOWN = 3  # seconds uvicorn waits for open connections before it cancels them
UI_INDEX = "index.html"
# the built UI is the only page that may load: same-origin scripts, no framing, no plugins
CSP = (
    "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "font-src 'self' data:; connect-src 'self'; form-action 'none'; "
    "frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}
LOG = logging.getLogger("agent_harness.server")
TASK_ID = re.compile(r"T-[0-9]+")
HOST = re.compile(r"(localhost|127\.0\.0\.1|\[::1\])(:[0-9]{1,5})?", re.IGNORECASE | re.ASCII)


def is_loopback_host(header: str) -> bool:
    """True only for a well-formed loopback Host header (name, optional numeric port)."""
    return HOST.fullmatch(header.strip(" \t")) is not None


def require_loopback(host: str) -> None:
    if host not in LOOPBACK:
        raise HarnessError(f"the server only binds to loopback ({', '.join(sorted(LOOPBACK))})")


def _number(task_id: str) -> int:
    return int(task_id.split("-", 1)[1])


def _summary(task: Task) -> dict:
    verified = sum(1 for item in task.acceptance if item.verified_by)
    return {
        "id": task.id,
        "title": task.title,
        "owner": task.owner,
        "status": task.status,
        "scope": task.scope,
        "acceptance": len(task.acceptance),
        "verified": verified,
        "review": task.review.status if task.review else None,
    }


def check_token(token: str) -> None:
    if len(token) < MIN_TOKEN:
        raise HarnessError(f"the token must be at least {MIN_TOKEN} characters")


def check_port(port: int) -> None:
    if not 1 <= port <= 65535:
        raise HarnessError("the port must be between 1 and 65535")


def is_api_path(path: str) -> bool:
    """True for anything that names the api folder, also on case-folding file systems
    (`API/x`), with a Windows separator (`api\\x`) or with trailing dots or spaces (`api./x`)."""
    first = path.replace("\\", "/").split("/", 1)[0]
    return first.casefold().rstrip(". ") == "api"


class UiFiles(StaticFiles):
    """The built UI, which never answers under /api (a folder named api must not leak tokenless)."""

    async def get_response(self, path, scope):
        if is_api_path(path):
            raise HTTPException(404)
        return await super().get_response(path, scope)


def check_ui(ui: Path | None) -> Path | None:
    """The folder of a built UI, or None; refuses a folder that cannot be served."""
    if ui is None:
        return None
    folder = Path(ui).resolve()
    index = folder / UI_INDEX
    # an index.html that is a link out of the folder would start the server and then 404
    if not folder.is_dir() or not index.is_file() or not index.resolve().is_relative_to(folder):
        raise HarnessError(f"{folder} is not a built UI: it needs a {UI_INDEX} inside it")
    return folder


def default_engine() -> object:
    """The engine the CLI uses; resolved late so tests and the CLI can replace it."""
    from agent_harness import cli

    return cli.make_engine()


def create_app(
    project: Path,
    token: str,
    *,
    engine_factory: Callable[[], object] | None = None,
    ui: Path | None = None,
) -> FastAPI:
    """The app for one project; `token` is the shared secret every request must carry.

    `ui` is a folder with a built UI, served read-only and without a token (it holds no
    secret); the API keeps requiring the token.
    """
    check_token(token)
    ui_folder = check_ui(ui)
    root = project_root(project)
    jobs = JobManager(root, engine_factory or default_engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:  # also when the app body fails, so no job is left running
            await jobs.shutdown()

    app = FastAPI(
        title="Agent harness",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.jobs = jobs
    bearer = HTTPBearer(auto_error=False)

    def authorize(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> None:
        given = credentials.credentials if credentials else ""
        if not secrets.compare_digest(given.encode(), token.encode()):
            raise HTTPException(401, "missing or wrong token")

    @app.middleware("http")
    async def only_loopback_hosts(request: Request, call_next):
        if not is_loopback_host(request.headers.get("host", "")):
            return JSONResponse({"detail": "host not allowed"}, status_code=403)
        return await call_next(request)

    @app.middleware("http")  # added last, so it wraps the host check and its 403 too
    async def security_headers(request: Request, call_next):
        try:
            response = await call_next(request)
        except Exception:  # noqa: BLE001 - an unhandled error must still carry the headers
            LOG.exception("unhandled error on %s %s", request.method, request.url.path)
            response = JSONResponse({"detail": "internal error"}, status_code=500)
        response.headers.update(SECURITY_HEADERS)
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(HarnessError)
    async def harness_error(_: Request, exc: HarnessError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(OSError)
    @app.exception_handler(UnicodeError)
    async def unreadable(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": "a ledger file could not be read"}, status_code=422)

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    @app.get("/api/plan", dependencies=[Depends(authorize)])
    def get_plan() -> dict:
        return plan(root).model_dump(mode="json")

    @app.get("/api/check", dependencies=[Depends(authorize)])
    def get_check() -> dict:
        return check_ledger(root).model_dump(mode="json")

    @app.get("/api/tasks", dependencies=[Depends(authorize)])
    def list_tasks() -> list[dict]:
        folder = root / TASKS_DIR
        names = sorted(
            (path.stem for path in folder.glob("T-*.yaml") if TASK_ID.fullmatch(path.stem)),
            key=_number,
        )
        rows = []
        for task_id in names:
            try:
                rows.append(_summary(_load_task(managed_path(root, f"{TASKS_DIR}/{task_id}.yaml"))))
            except HarnessError as exc:  # one broken file must not hide the others
                rows.append({"id": task_id, "error": str(exc)})
            except (OSError, UnicodeError):
                rows.append({"id": task_id, "error": "the file could not be read"})
        return rows

    @app.get("/api/tasks/{task_id}", dependencies=[Depends(authorize)])
    def get_task(task_id: str) -> dict:
        if not TASK_ID.fullmatch(task_id):
            raise HTTPException(404, "no such task")
        path = managed_path(root, f"{TASKS_DIR}/{task_id}.yaml")
        if not path.is_file():
            raise HTTPException(404, "no such task")
        return _load_task(path).model_dump(mode="json")

    @app.post("/api/jobs", status_code=202, dependencies=[Depends(authorize)])
    async def start_job(request: Request) -> dict:
        media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if media_type != "application/json":
            raise HTTPException(415, "send application/json")
        declared = request.headers.get("content-length", "")
        if declared.isascii() and declared.isdigit():
            # a number with more digits than any real size is too large (and int() would balk)
            if len(declared) > MAX_ID_DIGITS or int(declared) > MAX_BODY:
                raise HTTPException(413, "the request body is too large")
        chunks, total = [], 0
        async for chunk in request.stream():  # stop reading at the cap, not after the upload
            total += len(chunk)
            if total > MAX_BODY:
                raise HTTPException(413, "the request body is too large")
            chunks.append(chunk)
        body = b"".join(chunks)
        try:
            params = JobParams.model_validate_json(body)
        except ValidationError as exc:
            problems = [
                {"field": ".".join(str(part) for part in e["loc"]), "problem": e["msg"]}
                for e in exc.errors(include_input=False)
            ]
            raise HTTPException(422, problems) from exc
        try:
            job = jobs.start(params)
        except Busy as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"id": job.id, "status": job.status}

    # async on purpose: they read the job table, which only the event loop may touch
    @app.get("/api/jobs", dependencies=[Depends(authorize)])
    async def list_jobs() -> list[dict]:
        return [job.view() for job in reversed(list(jobs.jobs.values()))]

    def find_job(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        return job

    @app.get("/api/jobs/{job_id}", dependencies=[Depends(authorize)])
    async def get_job(job_id: str) -> dict:
        return find_job(job_id).view()

    @app.get("/api/jobs/{job_id}/events", dependencies=[Depends(authorize)])
    async def job_events(
        job_id: str,
        after: Annotated[int, Query(ge=0)] = 0,
        last_event_id: Annotated[str | None, Header()] = None,
    ) -> StreamingResponse:
        job = find_job(job_id)
        if (
            last_event_id
            and last_event_id.isascii()
            and last_event_id.isdigit()
            and len(last_event_id) <= MAX_ID_DIGITS  # a longer one is not a real event id
        ):
            after = max(after, int(last_event_id))

        async def frames():
            async for event in jobs.stream(job, after):
                if event is None:
                    yield ": keepalive\n\n"
                else:
                    yield f"id: {event['id']}\ndata: {json.dumps(event)}\n\n"

        return StreamingResponse(
            frames(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    @app.post("/api/jobs/{job_id}/cancel", dependencies=[Depends(authorize)])
    async def cancel_job(job_id: str) -> dict:
        job = find_job(job_id)
        try:
            await jobs.cancel(job)
        except NotRunning as exc:
            raise HTTPException(409, str(exc)) from exc
        return job.view()

    if ui_folder is not None:  # last, so it never shadows an /api route
        app.mount("/", UiFiles(directory=ui_folder, html=True), name="ui")
    return app


def uvicorn_options(host: str, port: int) -> dict:
    """How the server is run; tests start a real server with exactly these options."""
    return {
        "host": host,
        "port": port,
        "log_level": "warning",
        "timeout_graceful_shutdown": GRACEFUL_SHUTDOWN,
    }


def serve(project: Path, *, host: str, port: int, token: str, ui: Path | None = None) -> None:
    """Run the server until interrupted. Needs the `server` extra."""
    require_loopback(host)
    check_port(port)
    app = create_app(project, token, ui=ui)
    try:
        import uvicorn
    except ImportError as exc:
        raise HarnessError(
            "the server needs its extra: pip install 'agent-harness-starter[server]'"
        ) from exc
    uvicorn.run(app, **uvicorn_options(host, port))
