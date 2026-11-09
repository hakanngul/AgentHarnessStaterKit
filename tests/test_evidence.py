import json
import shlex
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from agent_harness.check import TASKS_DIR
from agent_harness.cli import app
from agent_harness.filesystem import MAX_FILE_BYTES, sha256_file

runner = CliRunner()
PY = sys.executable


def make_task(root: Path, text: str | None = None) -> Path:
    target = root / TASKS_DIR / "T-1.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        text
        or "# keep me? no: evidence run rewrites the file\n"
        "id: T-1\ntitle: Demo\nowner: developer\nstatus: IN_PROGRESS\n"
        "acceptance:\n  - {id: AC1, text: Works}\n"
    )
    return target


def run(root: Path, *args: str):
    result = runner.invoke(app, ["evidence", "run", "--path", str(root), "--json", *args])
    return result.exit_code, json.loads(result.output)


def check(root: Path):
    result = runner.invoke(app, ["check", str(root), "--json"])
    return result.exit_code, json.loads(result.output)


def test_passing_command_is_recorded(tmp_path):
    make_task(tmp_path)
    code, out = run(tmp_path, "T-1", "--", PY, "-c", "print('3 passed')")
    assert code == 0, out
    evidence = out["evidence"]
    assert evidence["id"] == "E1"
    assert evidence["exit_code"] == 0
    assert evidence["summary"] == "3 passed"
    assert evidence["recorded_by"] == "harness"
    assert shlex.split(evidence["command"]) == [PY, "-c", "print('3 passed')"]
    output = tmp_path / evidence["output"]
    assert output.read_text() == "3 passed\n"
    assert evidence["output_sha256"] == sha256_file(output)
    task = yaml.safe_load((tmp_path / TASKS_DIR / "T-1.yaml").read_text())
    assert task["evidence"][0]["id"] == "E1"
    assert task["acceptance"] == [{"id": "AC1", "text": "Works"}]
    assert check(tmp_path)[0] == 0


def test_failing_command_is_recorded_and_exits_1(tmp_path):
    make_task(tmp_path)
    code, out = run(tmp_path, "T-1", "--", PY, "-c", "import sys; print('boom'); sys.exit(3)")
    assert code == 1
    assert out["ok"] is False
    assert out["evidence"]["exit_code"] == 3
    assert out["evidence"]["summary"] == "boom"
    assert check(tmp_path)[0] == 0


def test_ids_increment_and_explicit_id_is_unique(tmp_path):
    make_task(tmp_path)
    run(tmp_path, "T-1", "--", PY, "-c", "pass")
    code, out = run(tmp_path, "T-1", "--", PY, "-c", "pass")
    assert out["evidence"]["id"] == "E2"
    code, out = run(tmp_path, "T-1", "--id", "E7", "--", PY, "-c", "pass")
    assert out["evidence"]["id"] == "E7"
    code, out = run(tmp_path, "T-1", "--id", "E7", "--", PY, "-c", "pass")
    assert code == 1 and "already has evidence E7" in out["errors"][0]


def test_stderr_is_captured_and_no_shell_is_used(tmp_path):
    make_task(tmp_path)
    marker = tmp_path / "injected"
    code, out = run(
        tmp_path,
        "T-1",
        "--",
        PY,
        "-c",
        "import sys; print(sys.argv[1]); sys.stderr.write('err\\n')",
        f"; touch {marker}",
    )
    assert code == 0
    text = (tmp_path / out["evidence"]["output"]).read_text()
    assert f"; touch {marker}" in text and "err" in text
    assert not marker.exists()


def test_cwd_and_timeout(tmp_path):
    make_task(tmp_path)
    (tmp_path / "app").mkdir()
    code, out = run(
        tmp_path, "T-1", "--cwd", "./app", "--", PY, "-c", "import os; print(os.getcwd())"
    )
    assert out["evidence"]["cwd"] == "app"
    assert out["evidence"]["summary"].endswith("app")
    code, out = run(
        tmp_path, "T-1", "--timeout", "0.5", "--", PY, "-c", "import time; time.sleep(5)"
    )
    assert code == 1
    assert out["evidence"]["exit_code"] == 124
    assert "timed out" in out["evidence"]["summary"]


def test_large_output_is_truncated_and_still_hashed(tmp_path):
    make_task(tmp_path)
    code, out = run(tmp_path, "T-1", "--", PY, "-c", f"print('x' * {MAX_FILE_BYTES * 2})")
    output = tmp_path / out["evidence"]["output"]
    assert output.stat().st_size <= MAX_FILE_BYTES
    assert output.read_text().startswith("[harness: output truncated")
    assert check(tmp_path)[0] == 0


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["T-1", "--", "definitely-not-a-command-xyz"], "Could not start command"),
        (["T-9", "--", PY, "-c", "pass"], "missing"),
        (["../T-1", "--", PY, "-c", "pass"], "Invalid task id"),
        (["DEC-1", "--", PY, "-c", "pass"], "Invalid task id"),
        (["T-1", "--id", "X1", "--", PY, "-c", "pass"], "Invalid evidence id"),
        (["T-1", "--cwd", "../out", "--", PY, "-c", "pass"], "Unsafe managed path"),
        (["T-1", "--cwd", "nope", "--", PY, "-c", "pass"], "Working directory not found"),
        (["T-1", "--timeout", "0", "--", PY, "-c", "pass"], "positive finite"),
        (["T-1", "--timeout", "nan", "--", PY, "-c", "pass"], "positive finite"),
        (["T-1", "--timeout", "inf", "--", PY, "-c", "pass"], "positive finite"),
        (["T-1", "--", PY, "-c", "x" * 2100], "longer than 2000"),
    ],
)
def test_errors_write_nothing(tmp_path, args, expected):
    task = make_task(tmp_path)
    before = task.read_bytes()
    code, out = run(tmp_path, *args)
    assert code == 1
    assert expected in out["errors"][0], out
    assert task.read_bytes() == before
    assert not (tmp_path / ".harness/evidence").exists()


def test_invalid_task_file_is_not_touched(tmp_path):
    task = make_task(tmp_path, "id: T-1\ntitle: x\nowner: d\nstatus: WRONG\n")
    code, out = run(tmp_path, "T-1", "--", PY, "-c", "pass")
    assert code == 1 and "run `harness check`" in out["errors"][0]
    assert task.read_text().endswith("status: WRONG\n")
    assert not (tmp_path / ".harness/evidence").exists()


def test_existing_output_file_is_not_overwritten(tmp_path):
    make_task(tmp_path)
    target = tmp_path / ".harness/evidence/T-1/E1.txt"
    target.parent.mkdir(parents=True)
    target.write_text("user file\n")
    code, out = run(tmp_path, "T-1", "--id", "E1", "--", PY, "-c", "pass")
    assert code == 1 and "Refusing to overwrite" in out["errors"][0]
    assert target.read_text() == "user file\n"
    code, out = run(tmp_path, "T-1", "--", PY, "-c", "pass")
    assert code == 0 and out["evidence"]["id"] == "E2"
    assert target.read_text() == "user file\n"


def test_task_edits_during_the_run_are_kept(tmp_path):
    task = make_task(tmp_path)
    script = (
        f"import pathlib; p = pathlib.Path({str(task)!r});"
        "p.write_text(p.read_text().replace('IN_PROGRESS', 'IN_REVIEW'))"
    )
    code, out = run(tmp_path, "T-1", "--", PY, "-c", script)
    assert code == 0, out
    data = yaml.safe_load(task.read_text())
    assert data["status"] == "IN_REVIEW"
    assert data["evidence"][0]["id"] == "E1"


def test_conflicting_id_does_not_delete_the_other_runs_output(tmp_path):
    task = make_task(tmp_path)
    other = tmp_path / ".harness/evidence/T-1/E1.txt"
    script = (
        f"import pathlib, yaml; t = pathlib.Path({str(task)!r});"
        "d = yaml.safe_load(t.read_text());"
        "d['evidence'] = [{'id': 'E1', 'command': 'x', 'exit_code': 0}];"
        "t.write_text(yaml.safe_dump(d))"
    )
    code, out = run(tmp_path, "T-1", "--", PY, "-c", script)
    assert code == 1 and "gained evidence E1 while running" in out["errors"][0]
    assert not other.exists()  # this run's own reserved file is removed
    assert yaml.safe_load(task.read_text())["evidence"] == [
        {"id": "E1", "command": "x", "exit_code": 0}
    ]


def test_lock_held_by_another_writer(tmp_path, monkeypatch):
    task = make_task(tmp_path)
    monkeypatch.setattr("agent_harness.evidence.LOCK_WAIT_SECONDS", 0.2)
    lock = task.with_name(".T-1.yaml.lock")
    lock.write_text("")
    code, out = run(tmp_path, "T-1", "--", PY, "-c", "pass")
    assert code == 1 and "held by another writer" in out["errors"][0]
    assert lock.exists()
    assert not (tmp_path / ".harness/evidence/T-1").exists()
    assert "evidence" not in yaml.safe_load(task.read_text())


def test_json_output_summary_uses_last_meaningful_line(tmp_path):
    make_task(tmp_path)
    script = "import json; print(json.dumps({'ok': True}, indent=2))"
    code, out = run(tmp_path, "T-1", "--", PY, "-c", script)
    assert out["evidence"]["summary"] == '"ok": true'


def test_stale_temp_file_is_not_deleted(tmp_path):
    task = make_task(tmp_path)
    stale = task.with_name(".T-1.yaml.tmp")
    stale.write_text("someone else's temp\n")
    code, out = run(tmp_path, "T-1", "--", PY, "-c", "pass")
    assert code == 0
    assert stale.read_text() == "someone else's temp\n"


def test_failed_task_write_removes_output(tmp_path, monkeypatch):
    make_task(tmp_path)

    def broken(path, text):
        raise OSError("disk full")

    monkeypatch.setattr("agent_harness.evidence._write_atomic", broken)
    code, out = run(tmp_path, "T-1", "--", PY, "-c", "print('x')")
    assert code == 1 and "disk full" in out["errors"][0]
    assert not (tmp_path / ".harness/evidence").exists()
    assert "evidence" not in yaml.safe_load((tmp_path / TASKS_DIR / "T-1.yaml").read_text())


def test_check_detects_edited_output(tmp_path):
    make_task(tmp_path)
    code, out = run(tmp_path, "T-1", "--", PY, "-c", "import sys; sys.exit(1)")
    output = tmp_path / out["evidence"]["output"]
    output.write_text("all tests passed\n")
    code, report = check(tmp_path)
    assert code == 1
    assert any("output changed after it was recorded" in e for e in report["errors"])


@pytest.mark.parametrize(
    "evidence",
    [
        {"id": "E1", "command": "x", "exit_code": 0, "recorded_by": "harness"},
        {"id": "E1", "kind": "manual", "result": "pass", "summary": "s", "recorded_by": "harness"},
        {"id": "E1", "command": "x", "exit_code": 0, "output": "o.txt", "recorded_by": "harness"},
    ],
)
def test_harness_evidence_requires_output_and_hash(tmp_path, evidence):
    make_task(
        tmp_path,
        yaml.safe_dump(
            {"id": "T-1", "title": "x", "owner": "d", "status": "BACKLOG", "evidence": [evidence]}
        ),
    )
    code, report = check(tmp_path)
    assert code == 1
    assert any("needs kind command, output and hash" in e for e in report["errors"])


def test_commit_is_read_before_the_command_runs(tmp_path, monkeypatch):
    make_task(tmp_path)
    calls = []
    monkeypatch.setattr(
        "agent_harness.evidence._commit", lambda root: calls.append("commit") or None
    )
    original = __import__("subprocess").run

    def tracking(*args, **kwargs):
        calls.append("run")
        return original(*args, **kwargs)

    monkeypatch.setattr("agent_harness.evidence.subprocess.run", tracking)
    code, out = run(tmp_path, "T-1", "--", PY, "-c", "pass")
    assert calls == ["commit", "run"]
    assert out["warnings"] == ["No git commit recorded (not a Git checkout?)"]


@pytest.mark.parametrize(
    ("printed", "secret"),
    [
        ("GITHUB_TOKEN=ghp_" + "a" * 36, "ghp_" + "a" * 36),
        ("password: hunter2-secret", "hunter2-secret"),
        ("Authorization: Bearer abc.def.ghi", "abc.def.ghi"),
        ("key sk-" + "b" * 40, "sk-" + "b" * 40),
        ("aws AKIA" + "C" * 16, "AKIA" + "C" * 16),
    ],
)
def test_common_secrets_are_redacted(tmp_path, printed, secret):
    make_task(tmp_path)
    code, out = run(tmp_path, "T-1", "--", PY, "-c", f"print({printed!r})", printed)
    text = (tmp_path / out["evidence"]["output"]).read_text()
    assert secret not in text and "[REDACTED]" in text
    assert secret not in out["evidence"]["command"]
    assert secret not in (tmp_path / TASKS_DIR / "T-1.yaml").read_text()
    assert check(tmp_path)[0] == 0


def test_output_is_streamed_not_buffered(tmp_path, monkeypatch):
    make_task(tmp_path)
    seen = {}
    original = __import__("subprocess").run

    def tracking(*args, **kwargs):
        seen["stdout"] = kwargs.get("stdout")
        return original(*args, **kwargs)

    monkeypatch.setattr("agent_harness.evidence.subprocess.run", tracking)
    run(tmp_path, "T-1", "--", PY, "-c", "print('x')")
    assert hasattr(seen["stdout"], "write")  # a file, not subprocess.PIPE


def test_task_file_size_limit(tmp_path, monkeypatch):
    task = make_task(tmp_path)
    monkeypatch.setattr("agent_harness.evidence.MAX_FILE_BYTES", 150)
    before = task.read_text()
    code, out = run(tmp_path, "T-1", "--", PY, "-c", "pass")
    assert code == 1 and "would exceed 150 bytes" in out["errors"][0]
    assert task.read_text() == before
