import hashlib
from pathlib import Path

import pytest
import yaml
from fastapi import testclient as fastapi_testclient
from test_engine_runner import project, task

from agent_harness.filesystem import HarnessError
from agent_harness.server import (
    check_port,
    create_app,
    is_loopback_host,
    require_loopback,
)

TOKEN = "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def client(root: Path, token: str = TOKEN, **options):
    app = create_app(root, token, **options)
    return fastapi_testclient.TestClient(app, base_url="http://localhost")


def snapshot(root: Path) -> dict[str, tuple]:
    """Every file and directory with size and mtime, so any created or touched entry shows."""
    entries = {}
    for item in sorted(root.rglob("*")):
        info = item.stat()
        content = item.read_bytes() if item.is_file() else None
        entries[str(item)] = (item.is_dir(), info.st_size, info.st_mtime_ns, content)
    return entries


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
    assert api.get("/api/tasks/T-%D9%A1", headers=AUTH).status_code == 404  # Arabic-Indic digit
    assert api.get("/api/tasks/T-1%0A", headers=AUTH).status_code == 404
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


@pytest.mark.parametrize(
    "header",
    [
        "localhost",
        "localhost:8765",
        "127.0.0.1:1",
        "[::1]:8765",
        "[::1]",
        "LOCALHOST",
        " localhost ",
    ],
)
def test_well_formed_loopback_hosts_are_accepted(header):
    assert is_loopback_host(header)


@pytest.mark.parametrize(
    "header",
    [
        "",
        "evil.example",
        "localhost.",
        "localhost:abc",
        "localhost:",
        "localhost:123456",
        "[::1]junk",
        "[::1",
        "::1",
        "a:b:c",
        "localhost@evil",
        "127.0.0.1.evil.example",
        "127.0.0.2",
    ],
)
def test_malformed_or_foreign_hosts_are_rejected(header):
    assert not is_loopback_host(header)


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


def test_unreadable_files_are_422_or_an_error_row_never_a_500(tmp_path):
    root = project(tmp_path, task("T-1"), task("T-2"))
    (root / ".harness/tasks/T-2.yaml").write_bytes(b"\xff\xfe\x00 not utf-8 \x80")
    api = client(root)
    one = api.get("/api/tasks/T-2", headers=AUTH)
    assert one.status_code == 422 and str(root) not in one.text
    rows = api.get("/api/tasks", headers=AUTH)
    assert rows.status_code == 200 and rows.json()[0]["id"] == "T-1" and "error" in rows.json()[1]
    for url in ("/api/plan", "/api/check"):
        assert api.get(url, headers=AUTH).status_code in (200, 422), url


def test_a_corrupt_ledger_is_reported_in_the_plan_and_check_bodies(tmp_path):
    root = project(tmp_path, task("T-1"), task("T-2"))
    (root / ".harness/tasks/T-2.yaml").write_text("id: [unclosed")
    api = client(root)
    plan = api.get("/api/plan", headers=AUTH)
    check = api.get("/api/check", headers=AUTH)
    assert plan.status_code == 200 and plan.json()["errors"] and plan.json()["start"] == []
    assert check.status_code == 200 and check.json()["errors"]


def test_a_missing_config_is_422_not_a_crash(tmp_path):
    root = project(tmp_path, task("T-1"))
    (root / ".harness/config.yaml").unlink()
    api = client(root)
    for url in ("/api/plan", "/api/check"):
        assert api.get(url, headers=AUTH).status_code in (200, 422), url


def test_auth_edge_cases(tmp_path):
    api = client(project(tmp_path, task("T-1")))
    assert api.get("/api/plan", headers={"Authorization": f"bearer {TOKEN}"}).status_code == 200
    assert api.get("/api/plan", headers={"Authorization": "Bearer "}).status_code == 401
    assert api.get("/api/plan", headers={"Authorization": f"Basic {TOKEN}"}).status_code == 401
    assert api.head("/api/plan").status_code in (401, 405)
    assert api.options("/api/plan").status_code in (401, 405)
    assert api.options("/api/plan", headers={"Origin": "http://evil.example"}).status_code in (
        401,
        405,
    )
    got = api.options("/api/plan", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in got.headers  # no CORS: pages cannot read it


def test_a_foreign_host_is_refused_before_the_token_is_looked_at(tmp_path):
    api = fastapi_testclient.TestClient(create_app(project(tmp_path, task("T-1")), TOKEN))
    wrong = {"Authorization": "Bearer wrong", "Host": "evil.example"}
    assert api.get("/api/plan", headers=wrong).status_code == 403


def test_the_port_must_be_a_real_port():
    for port in (0, -1, 65536):
        with pytest.raises(HarnessError, match="port"):
            check_port(port)
    check_port(1)
    check_port(65535)


class FakeUvicorn:
    def __init__(self):
        self.calls = []

    def run(self, app, **kwargs):
        self.calls.append((app, kwargs))


def test_serve_hands_the_loopback_host_and_port_to_uvicorn(monkeypatch, tmp_path):
    import sys

    from agent_harness.server import serve

    fake = FakeUvicorn()
    monkeypatch.setitem(sys.modules, "uvicorn", fake)
    root = project(tmp_path, task("T-1"))
    serve(root, host="127.0.0.1", port=9123, token=TOKEN)
    assert len(fake.calls) == 1
    assert fake.calls[0][1]["host"] == "127.0.0.1" and fake.calls[0][1]["port"] == 9123
    from agent_harness.server import uvicorn_options

    assert fake.calls[0][1] == uvicorn_options("127.0.0.1", 9123)  # incl. the grace period
    assert fake.calls[0][1]["timeout_graceful_shutdown"] == 3


def test_serve_without_uvicorn_names_the_extra(monkeypatch, tmp_path):
    import sys

    from agent_harness.server import serve

    monkeypatch.setitem(sys.modules, "uvicorn", None)  # `import uvicorn` raises ImportError
    root = project(tmp_path, task("T-1"))
    with pytest.raises(HarnessError, match=r"agent-harness-starter\[server\]"):
        serve(root, host="127.0.0.1", port=9123, token=TOKEN)


def test_serve_never_echoes_a_token_the_user_supplied(monkeypatch, tmp_path):
    secret = "s3cret-" + "x" * 30
    result, calls = run_serve(monkeypatch, tmp_path, token=secret)
    assert result.exit_code == 0 and secret not in result.output and "token:" not in result.output
    assert calls[0][1]["token"] == secret


def test_serve_prints_nothing_secret_when_the_start_is_refused(monkeypatch, tmp_path):
    for args, env in ((["--host", "0.0.0.0"], None), (["--port", "0"], None), ([], "short")):  # noqa: S104
        result, calls = run_serve(monkeypatch, tmp_path, *args, token=env)
        assert result.exit_code == 1 and calls == [], args
        assert "token:" not in result.output, args


def test_the_banner_has_no_scheme_and_a_plain_token_line(monkeypatch, tmp_path):
    result, calls = run_serve(monkeypatch, tmp_path)
    lines = result.output.strip().splitlines()
    assert (
        lines[0] == "listening on 127.0.0.1:8765" and lines[1] == f"token: {calls[0][1]['token']}"
    )
    assert "http://" not in result.output


@pytest.mark.parametrize(
    "error",
    [PermissionError("/secret/path/T-2.yaml"), UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")],
)
def test_an_os_or_unicode_error_is_422_or_an_error_row_without_the_path(
    monkeypatch, tmp_path, error
):
    from agent_harness import server

    root = project(tmp_path, task("T-1"), task("T-2"))
    real = server._load_task

    def flaky(path):
        if path.name == "T-2.yaml":
            raise error
        return real(path)

    monkeypatch.setattr(server, "_load_task", flaky)
    api = client(root)
    one = api.get("/api/tasks/T-2", headers=AUTH)
    assert one.status_code == 422 and "/secret/path" not in one.text
    rows = api.get("/api/tasks", headers=AUTH)
    assert rows.status_code == 200
    assert "status" in rows.json()[0] and "error" in rows.json()[1]
    assert "/secret/path" not in rows.text


def test_the_task_id_pattern_takes_ascii_digits_only():
    from agent_harness.server import TASK_ID

    assert TASK_ID.fullmatch("T-12")
    for bad in ("T-\u0661", "T-", "t-1", "T-1\n", "T-1.yaml", "T-1/.."):
        assert TASK_ID.fullmatch(bad) is None, bad


def test_serve_refuses_a_bad_host_or_port_without_touching_uvicorn(monkeypatch, tmp_path):
    import sys

    from agent_harness.server import serve

    fake = FakeUvicorn()
    monkeypatch.setitem(sys.modules, "uvicorn", fake)
    root = project(tmp_path, task("T-1"))
    for host, port in (("0.0.0.0", 9000), ("example.com", 9000), ("127.0.0.1", 0)):  # noqa: S104
        with pytest.raises(HarnessError):
            serve(root, host=host, port=port, token=TOKEN)
    with pytest.raises(HarnessError, match="token"):
        serve(root, host="127.0.0.1", port=9000, token="short")
    assert fake.calls == []


def test_serve_prints_nothing_for_a_missing_project(monkeypatch, tmp_path):
    result, calls = run_serve(monkeypatch, tmp_path / "does-not-exist")
    assert result.exit_code == 1 and calls == []
    assert "token:" not in result.output and "listening" not in result.output


def test_a_non_ascii_lookalike_or_unicode_space_is_not_loopback():
    assert not is_loopback_host("localhoſt")  # long s folds to s without re.ASCII
    assert not is_loopback_host("\u00a0localhost")
    assert not is_loopback_host("localhost\u0085")


def test_an_undecodable_config_does_not_crash_the_api(tmp_path):
    root = project(tmp_path, task("T-1"))
    (root / ".harness/config.yaml").write_bytes(b"\xff\xfe\x00 bad \x80")
    api = client(root)
    for url in ("/api/plan", "/api/check"):
        response = api.get(url, headers=AUTH)
        assert response.status_code in (200, 422), url
        assert str(root) not in response.text or response.status_code == 200


# --- browser readiness (T-14) -------------------------------------------------------------

from agent_harness.server import CSP, SECURITY_HEADERS, check_ui  # noqa: E402


def ui_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "ui"
    (folder / "assets").mkdir(parents=True)
    (folder / "index.html").write_text("<!doctype html><title>harness</title>")
    (folder / "assets/app.js").write_text("console.log('ui')")
    (tmp_path / "secret.txt").write_text("SECRET-OUTSIDE")
    return folder


EXPECTED_CSP = (
    "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "font-src 'self' data:; connect-src 'self'; form-action 'none'; "
    "frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
)
EXPECTED_HEADERS = {  # literals on purpose: a test that imports the constant cannot catch an edit
    "content-security-policy": EXPECTED_CSP,
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
}


def has_security_headers(response) -> bool:
    return all(response.headers.get(k) == v for k, v in EXPECTED_HEADERS.items())


@pytest.mark.parametrize(
    ("method", "url", "kwargs", "status"),
    [
        ("GET", "/api/health", {}, 200),
        ("GET", "/api/plan", {"headers": AUTH}, 200),
        ("GET", "/api/plan", {}, 401),
        ("GET", "/api/plan", {"headers": {**AUTH, "Host": "evil.example"}}, 403),
        ("GET", "/api/tasks/nope", {"headers": AUTH}, 404),
        ("DELETE", "/api/health", {}, 405),
        ("POST", "/api/jobs", {"headers": {**AUTH, "Content-Type": "text/plain"}}, 415),
        (
            "POST",
            "/api/jobs",
            {"headers": {**AUTH, "Content-Type": "application/json"}, "content": b"x" * 10_001},
            413,
        ),
    ],
)
def test_every_response_carries_the_security_headers(tmp_path, method, url, kwargs, status):
    api = client(project(tmp_path, task("T-1")))
    response = api.request(method, url, **kwargs)
    assert response.status_code == status
    assert has_security_headers(response), dict(response.headers)
    assert response.headers["cache-control"] == "no-store"  # nothing under /api is cached


def test_the_policy_and_header_values_are_pinned_to_literals():
    assert CSP == EXPECTED_CSP
    assert {k.lower(): v for k, v in SECURITY_HEADERS.items()} == EXPECTED_HEADERS
    assert "unsafe-eval" not in CSP and "*" not in CSP.replace("'self'", "")
    assert "script-src" not in CSP  # scripts fall back to default-src 'self': no inline script


def test_an_unhandled_error_is_a_500_that_still_carries_the_headers(monkeypatch, tmp_path):
    from agent_harness import server

    def boom(_root):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(server, "plan", boom)
    api = client(project(tmp_path, task("T-1")))
    response = api.get("/api/plan", headers=AUTH)
    assert response.status_code == 500 and response.json() == {"detail": "internal error"}
    assert has_security_headers(response) and response.headers["cache-control"] == "no-store"
    assert "secret internal detail" not in response.text


def test_there_is_no_cors_so_no_other_origin_may_read_or_preflight(tmp_path):
    api = client(project(tmp_path, task("T-1")))
    foreign = {"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"}
    preflight = api.options("/api/jobs", headers=foreign)
    reply = api.get("/api/plan", headers={**AUTH, "Origin": "https://evil.example"})
    for response in (preflight, reply):
        assert not [k for k in response.headers if k.lower().startswith("access-control-")]
    assert preflight.status_code == 405


def test_the_built_ui_is_served_without_a_token_and_the_api_still_needs_one(tmp_path):
    root = project(tmp_path, task("T-1"))
    api = client(root, ui=ui_folder(tmp_path))
    page = api.get("/")
    assert page.status_code == 200 and "<title>harness</title>" in page.text
    assert api.get("/assets/app.js").text == "console.log('ui')"
    assert has_security_headers(page)
    assert "no-store" not in page.headers.get("cache-control", "")  # only the API is no-store
    assert api.get("/api/health").json() == {"ok": True}  # not shadowed by the UI
    assert api.get("/api/plan").status_code == 401
    assert api.get("/api/plan", headers=AUTH).status_code == 200
    assert api.get("/api/nothing-here", headers=AUTH).status_code == 404


def test_a_folder_called_api_inside_the_ui_is_never_served(tmp_path):
    folder = ui_folder(tmp_path)
    (folder / "api").mkdir()
    (folder / "api/leak.txt").write_text("TOKENLESS-LEAK")
    api = client(project(tmp_path, task("T-1")), ui=folder)
    for url in ("/api/leak.txt", "/api/", "/api"):
        for headers in ({}, AUTH):
            response = api.get(url, headers=headers)
            assert "TOKENLESS-LEAK" not in response.text, url
    assert api.get("/api/leak.txt", headers=AUTH).status_code == 404


def test_without_a_ui_the_root_is_still_404(tmp_path):
    assert client(project(tmp_path, task("T-1"))).get("/").status_code == 404


def test_the_ui_is_read_only(tmp_path):
    api = client(project(tmp_path, task("T-1")), ui=ui_folder(tmp_path))
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        assert api.request(method, "/").status_code == 405, method
        assert api.request(method, "/assets/app.js").status_code == 405, method
    assert (tmp_path / "ui/assets/app.js").read_text() == "console.log('ui')"


@pytest.mark.parametrize(
    "url",
    [
        "/../secret.txt",
        "/%2e%2e/secret.txt",
        "/assets/../../secret.txt",
        "/assets/%2e%2e/%2e%2e/secret.txt",
        "/..%2fsecret.txt",
        "/assets/..%5c..%5csecret.txt",
        "/%00",
    ],
)
def test_the_ui_never_serves_a_file_outside_its_folder(tmp_path, url):
    api = client(project(tmp_path, task("T-1")), ui=ui_folder(tmp_path))
    response = api.get(url)
    assert "SECRET-OUTSIDE" not in response.text
    assert response.status_code in (400, 404)


def test_a_symlink_that_leaves_the_ui_folder_is_not_followed(tmp_path):
    folder = ui_folder(tmp_path)
    try:
        (folder / "leak.txt").symlink_to(tmp_path / "secret.txt")
    except OSError:
        pytest.skip("symlinks are not available")
    api = client(project(tmp_path, task("T-1")), ui=folder)
    response = api.get("/leak.txt")
    assert response.status_code == 404 and "SECRET-OUTSIDE" not in response.text


def test_the_ui_goes_through_the_host_check_too(tmp_path):
    api = client(project(tmp_path, task("T-1")), ui=ui_folder(tmp_path))
    for url in ("/", "/assets/app.js"):
        response = api.get(url, headers={"Host": "evil.example"})
        assert response.status_code == 403 and has_security_headers(response), url


def test_a_folder_that_is_not_a_built_ui_is_refused(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    (tmp_path / "file").write_text("x")
    (tmp_path / "dirindex").mkdir()
    (tmp_path / "dirindex/index.html").mkdir()
    root = project(tmp_path / "proj", task("T-1"))
    for bad in (tmp_path / "missing", empty, tmp_path / "file", tmp_path / "dirindex"):
        with pytest.raises(HarnessError, match="not a built UI"):
            check_ui(bad)
        with pytest.raises(HarnessError, match="not a built UI"):
            create_app(root, TOKEN, ui=bad)
    assert check_ui(None) is None
    assert check_ui(ui_folder(tmp_path)) == (tmp_path / "ui").resolve()


def test_serve_passes_the_ui_folder_on_and_refuses_a_bad_one_before_listening(
    monkeypatch, tmp_path
):
    folder = ui_folder(tmp_path)
    good, calls = run_serve(monkeypatch, tmp_path, "--ui", str(folder))
    assert good.exit_code == 0 and calls[0][1]["ui"] == folder.resolve()
    bad, more = run_serve(monkeypatch, tmp_path, "--ui", str(tmp_path / "missing"))
    assert bad.exit_code == 1 and "not a built UI" in bad.output
    assert more == [] and "listening" not in bad.output and "token:" not in bad.output
    plain, last = run_serve(monkeypatch, tmp_path)
    assert plain.exit_code == 0 and last[0][1]["ui"] is None
