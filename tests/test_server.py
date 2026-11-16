import hashlib
from pathlib import Path

import pytest
import yaml
from fastapi import testclient as fastapi_testclient
from test_engine_runner import project, task

from agent_harness.filesystem import HarnessError
from agent_harness.server import create_app, host_name, require_loopback

TOKEN = "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def client(root: Path, token: str = TOKEN):
    return fastapi_testclient.TestClient(create_app(root, token), base_url="http://localhost")


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(p): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_plan_tasks_and_check_return_the_real_ledger(tmp_path):
    root = project(
        tmp_path,
        task("T-1"),
        task("T-2", "CANCELLED", reason="dropped"),
        task("T-10", scope=["src/other/"]),
    )
    api = client(root)

    plan = api.get("/api/plan", headers=AUTH)
    assert plan.status_code == 200
    assert {item["id"] for item in plan.json()["start"]} == {"T-1", "T-10"}

    rows = api.get("/api/tasks", headers=AUTH).json()
    assert [row["id"] for row in rows] == ["T-1", "T-2", "T-10"]  # numeric order, not text
    assert (
        rows[1]["status"] == "CANCELLED"
        and rows[0]["acceptance"] == 1
        and rows[0]["review"] == "pending"
    )

    full = api.get("/api/tasks/T-1", headers=AUTH).json()
    assert full["goal"] == "Let users sign in." and full["scope"] == ["src/login/"]

    check = api.get("/api/check", headers=AUTH)
    assert check.status_code == 200 and "errors" in check.json()


def test_unknown_or_malformed_task_ids_are_404(tmp_path):
    api = client(project(tmp_path, task("T-1")))
    assert api.get("/api/tasks/T-9", headers=AUTH).status_code == 404
    assert api.get("/api/tasks/not-a-task", headers=AUTH).status_code == 404
    assert api.get("/api/tasks/T-1.yaml", headers=AUTH).status_code == 404
    assert api.get("/api/tasks/..%2Fconfig", headers=AUTH).status_code == 404


def test_a_broken_task_file_is_422_and_does_not_hide_the_others(tmp_path):
    root = project(tmp_path, task("T-1"), task("T-2"))
    (root / ".harness/tasks/T-2.yaml").write_text("id: [unclosed")
    api = client(root)
    assert api.get("/api/tasks/T-2", headers=AUTH).status_code == 422
    rows = api.get("/api/tasks", headers=AUTH).json()
    assert rows[0]["id"] == "T-1" and "status" in rows[0]
    assert rows[1]["id"] == "T-2" and "error" in rows[1]


def test_the_api_never_writes(tmp_path):
    root = project(tmp_path, task("T-1"))
    before = snapshot(root)
    api = client(root)
    for url in ("/api/health", "/api/plan", "/api/check", "/api/tasks", "/api/tasks/T-1"):
        api.get(url, headers=AUTH)
    assert snapshot(root) == before
    assert api.post("/api/plan", headers=AUTH).status_code == 405
    assert api.delete("/api/tasks/T-1", headers=AUTH).status_code == 405


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}]
)
def test_every_data_route_needs_the_token(tmp_path, headers):
    api = client(project(tmp_path, task("T-1")))
    for url in ("/api/plan", "/api/check", "/api/tasks", "/api/tasks/T-1"):
        assert api.get(url, headers=headers).status_code == 401, url
    assert api.get("/api/health").json() == {"ok": True}  # the only open route


def test_a_token_with_a_different_length_or_prefix_is_rejected(tmp_path):
    api = client(project(tmp_path, task("T-1")))
    for bad in (TOKEN[:-1], TOKEN + "x", TOKEN.upper()):
        assert api.get("/api/plan", headers={"Authorization": f"Bearer {bad}"}).status_code == 401


def test_a_foreign_host_header_is_403_even_with_the_right_token(tmp_path):
    root = project(tmp_path, task("T-1"))
    api = fastapi_testclient.TestClient(create_app(root, TOKEN))
    for host in ("evil.example", "evil.example:8765", "127.0.0.1.evil.example", "localhost.evil"):
        response = api.get("/api/plan", headers={**AUTH, "Host": host})
        assert response.status_code == 403, host
        assert api.get("/api/health", headers={"Host": host}).status_code == 403, host
    for host in ("127.0.0.1:8765", "localhost:1", "[::1]:8765", "LOCALHOST"):
        assert api.get("/api/plan", headers={**AUTH, "Host": host}).status_code == 200, host


def test_host_name_parsing():
    assert host_name("localhost:8765") == "localhost"
    assert host_name("[::1]:8765") == "::1"
    assert host_name("[::1]") == "::1"
    assert host_name("") == ""
    assert host_name("a:b:c") == "a:b:c"  # not a valid host:port, so not loopback either


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "example.com", "", "::"])  # noqa: S104
def test_binding_outside_loopback_is_refused(host):
    with pytest.raises(HarnessError, match="loopback"):
        require_loopback(host)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_binding_is_accepted(host):
    require_loopback(host)


def test_a_short_token_is_refused(tmp_path):
    with pytest.raises(HarnessError, match="token"):
        create_app(project(tmp_path, task("T-1")), "short")


def test_the_docs_routes_are_off(tmp_path):
    api = client(project(tmp_path, task("T-1")))
    for url in ("/docs", "/redoc", "/openapi.json"):
        assert api.get(url, headers=AUTH).status_code == 404


def test_a_project_without_ledger_errors_still_answers_the_plan(tmp_path):
    root = project(tmp_path, task("T-1", "DONE"))  # DONE without evidence is an error
    body = client(root).get("/api/plan", headers=AUTH).json()
    assert body["errors"] and body["start"] == []


def test_review_status_and_verified_count_are_reported(tmp_path):
    data = task(
        "T-1",
        "DONE",
        review={"status": "approved", "by": "reviewer", "notes": "ok"},
        acceptance=[{"id": "AC1", "text": "works", "verified_by": ["E1"]}],
        evidence=[
            {
                "id": "E1",
                "command": "pytest",
                "exit_code": 0,
                "recorded_by": "harness",
                "output": ".harness/evidence/T-1/E1.txt",
                "output_sha256": hashlib.sha256(b"ok\n").hexdigest(),
            }
        ],
    )
    root = project(tmp_path, data)
    row = client(root).get("/api/tasks", headers=AUTH).json()[0]
    assert row["review"] == "approved" and row["verified"] == 1
    assert yaml.safe_load((root / ".harness/tasks/T-1.yaml").read_text())["status"] == "DONE"


def run_serve(monkeypatch, tmp_path, *args, token=None):
    from typer.testing import CliRunner

    from agent_harness import server
    from agent_harness.cli import app

    calls = []
    monkeypatch.setattr(server, "serve", lambda path, **kw: calls.append((path, kw)))
    if token is None:
        monkeypatch.delenv("HARNESS_TOKEN", raising=False)
    else:
        monkeypatch.setenv("HARNESS_TOKEN", token)
    result = CliRunner().invoke(app, ["serve", str(tmp_path), *args])
    return result, calls


def test_serve_makes_a_fresh_token_and_prints_it_once(monkeypatch, tmp_path):
    first, calls = run_serve(monkeypatch, tmp_path, "--port", "9000")
    second, more = run_serve(monkeypatch, tmp_path)
    assert first.exit_code == 0 and second.exit_code == 0
    token = calls[0][1]["token"]
    assert len(token) >= 32 and first.output.count(token) == 1
    assert calls[0][1]["port"] == 9000 and calls[0][1]["host"] == "127.0.0.1"
    assert more[0][1]["token"] != token and more[0][1]["port"] == 8765


def test_serve_takes_the_token_from_the_environment(monkeypatch, tmp_path):
    result, calls = run_serve(monkeypatch, tmp_path, token="e" * 40)
    assert result.exit_code == 0 and calls[0][1]["token"] == "e" * 40


def test_serve_reports_a_refused_host_and_exits_1(monkeypatch, tmp_path):
    from agent_harness import server

    def refuse(path, **kw):
        raise HarnessError("the server only binds to loopback")

    from typer.testing import CliRunner

    from agent_harness.cli import app

    monkeypatch.setattr(server, "serve", refuse)
    result = CliRunner().invoke(app, ["serve", str(tmp_path), "--host", "0.0.0.0"])  # noqa: S104
    assert result.exit_code == 1 and "loopback" in result.output


def test_serve_without_the_extra_says_how_to_install_it(monkeypatch, tmp_path):
    import sys

    from typer.testing import CliRunner

    from agent_harness.cli import app

    monkeypatch.setitem(sys.modules, "agent_harness.server", None)  # import raises ImportError
    result = CliRunner().invoke(app, ["serve", str(tmp_path)])
    assert result.exit_code == 1 and "agent-harness-starter[server]" in result.output


def test_serve_really_binds_loopback_only_and_refuses_others(tmp_path):
    from agent_harness.server import serve

    root = project(tmp_path, task("T-1"))
    with pytest.raises(HarnessError, match="loopback"):
        serve(root, host="0.0.0.0", port=1, token=TOKEN)  # noqa: S104
