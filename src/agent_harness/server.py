"""Local HTTP server: a read-only JSON view of the ledger.

The server is a thin layer over the same functions the CLI uses. It listens on loopback
only and every request needs the bearer token, because a page in any browser tab can reach
`localhost`. This slice never writes and never starts an agent.
"""

import re
import secrets
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from agent_harness.check import TASKS_DIR
from agent_harness.check import check as check_ledger
from agent_harness.evidence import _load_task
from agent_harness.filesystem import HarnessError, managed_path, project_root
from agent_harness.models import Task
from agent_harness.schedule import plan

LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
MIN_TOKEN = 16
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


def create_app(project: Path, token: str) -> FastAPI:
    """The app for one project; `token` is the shared secret every request must carry."""
    check_token(token)
    root = project_root(project)
    app = FastAPI(title="Agent harness", docs_url=None, redoc_url=None, openapi_url=None)
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

    return app


def serve(project: Path, *, host: str, port: int, token: str) -> None:
    """Run the server until interrupted. Needs the `server` extra."""
    require_loopback(host)
    check_port(port)
    app = create_app(project, token)
    try:
        import uvicorn
    except ImportError as exc:
        raise HarnessError(
            "the server needs its extra: pip install 'agent-harness-starter[server]'"
        ) from exc
    uvicorn.run(app, host=host, port=port, log_level="warning")
