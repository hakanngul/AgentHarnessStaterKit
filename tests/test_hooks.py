import io
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from agent_harness.cli import app
from agent_harness.hooks import run_pre_tool_use

runner = CliRunner()
ROLES = {
    "developer": {"write": ["src/**"], "deny": ["src/secrets/**"]},
    "reviewer": {"write": [".harness/tasks/**"]},
}


def project(tmp_path: Path, roles: dict | None = ROLES) -> Path:
    data = {"project_name": "Demo"}
    if roles is not None:
        data["roles"] = roles
    (tmp_path / ".harness").mkdir(exist_ok=True)
    (tmp_path / ".harness/config.yaml").write_text(yaml.safe_dump(data))
    return tmp_path


def event(root: Path, file_path: str, tool: str = "Write", **extra) -> dict:
    key = "notebook_path" if tool == "NotebookEdit" else "file_path"
    return {
        "hook_event_name": "PreToolUse",
        "cwd": str(root),
        "tool_name": tool,
        "tool_input": {key: file_path},
        **extra,
    }


def hook(payload, env: dict | None = None):
    stdout, stderr = io.StringIO(), io.StringIO()
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    code = run_pre_tool_use(io.StringIO(raw), stdout, stderr, env or {})
    out = stdout.getvalue()
    return code, (json.loads(out) if out else None), stderr.getvalue()


def denied(result) -> str:
    code, out, _ = result
    assert code == 0
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    return out["hookSpecificOutput"]["permissionDecisionReason"]


def allowed(result) -> str:
    code, out, err = result
    assert code == 0 and out is None
    return err


@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit", "NotebookEdit"])
def test_allows_in_scope_and_denies_out_of_scope(tmp_path, tool):
    root = project(tmp_path)
    env = {"HARNESS_ROLE": "developer"}
    assert allowed(hook(event(root, str(root / "src/a.py"), tool), env)) == ""
    reason = denied(hook(event(root, str(root / "docs/a.md"), tool), env))
    assert "'developer' may not write docs/a.md" in reason


def test_deny_glob_wins(tmp_path):
    root = project(tmp_path)
    reason = denied(hook(event(root, str(root / "src/secrets/k")), {"HARNESS_ROLE": "developer"}))
    assert "denied by 'src/secrets/**'" in reason


def test_relative_path_uses_event_cwd(tmp_path):
    root = project(tmp_path)
    (root / "src").mkdir()
    payload = event(root, "a.py")
    payload["cwd"] = str(root / "src")
    assert allowed(hook(payload, {"HARNESS_ROLE": "developer"})) == ""


def test_outside_project_is_denied(tmp_path):
    (tmp_path / "p").mkdir()
    root = project(tmp_path / "p")
    reason = denied(hook(event(root, str(tmp_path / "x.py")), {"HARNESS_ROLE": "developer"}))
    assert "outside the project" in reason


def test_agent_type_is_used_only_when_it_is_a_role(tmp_path):
    root = project(tmp_path)
    target = str(root / "src/a.py")
    denied(hook(event(root, target, agent_type="reviewer")))
    note = allowed(hook(event(root, target, agent_type="Explore")))
    assert "role unknown" in note


def test_env_role_takes_precedence_over_agent_type(tmp_path):
    root = project(tmp_path)
    payload = event(root, str(root / "src/a.py"), agent_type="reviewer")
    assert allowed(hook(payload, {"HARNESS_ROLE": "developer"})) == ""


def test_unconfigured_env_role_is_denied(tmp_path):
    root = project(tmp_path)
    reason = denied(hook(event(root, str(root / "src/a.py")), {"HARNESS_ROLE": "develper"}))
    assert "'develper' is not defined" in reason


def test_claude_project_dir_wins_over_cwd(tmp_path):
    (tmp_path / "proj").mkdir()
    root = project(tmp_path / "proj")
    payload = event(tmp_path, str(root / "docs/x.md"))
    env = {"HARNESS_ROLE": "developer", "CLAUDE_PROJECT_DIR": str(root)}
    assert "may not write docs/x.md" in denied(hook(payload, env))


@pytest.mark.parametrize(
    ("setup", "payload", "note"),
    [
        ("no-roles", None, ""),
        ("no-config", None, ""),
        ("bad-config", None, "Invalid configuration"),
        ("ok", "not json", "not JSON"),
        ("ok", "[1, 2]", "not a JSON object"),
        ("ok", {"tool_name": "Write"}, ""),
        ("ok", {"tool_input": {"content": "x"}}, ""),
    ],
)
def test_non_enforcing_cases_do_not_block(tmp_path, setup, payload, note):
    if setup == "no-roles":
        project(tmp_path, roles=None)
    elif setup == "ok":
        project(tmp_path)
    elif setup == "bad-config":
        project(tmp_path, roles={"developer": {"write": ["../x"]}})
    if payload is None:
        payload = event(tmp_path, str(tmp_path / "docs/x.md"))
    result = hook(payload, {"HARNESS_ROLE": "developer"})
    assert note in allowed(result)


def test_oversized_input_does_not_block(tmp_path):
    project(tmp_path)
    assert "too large" in allowed(hook("x" * 1_048_577, {"HARNESS_ROLE": "developer"}))


def test_cli_hook_as_subprocess(tmp_path):
    """Exercise the real entry point exactly as Claude Code would."""
    root = project(tmp_path)
    payload = json.dumps(event(root, str(root / "docs/a.md")))
    result = subprocess.run(
        [sys.executable, "-m", "agent_harness", "hook", "pre-tool-use"],
        input=payload,
        capture_output=True,
        text=True,
        env={"HARNESS_ROLE": "developer", "PATH": ""},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    decision = json.loads(result.stdout)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"


def install(root: Path, *args: str):
    result = runner.invoke(app, ["hook", "install", str(root), "--json", *args])
    return result.exit_code, json.loads(result.output)


def test_install_creates_settings_and_is_idempotent(tmp_path):
    code, out = install(tmp_path)
    assert code == 0 and out["changed"] is True
    settings = json.loads((tmp_path / ".claude/settings.json").read_text())
    entry = settings["hooks"]["PreToolUse"][0]
    assert entry["matcher"] == "Write|Edit|MultiEdit|NotebookEdit"
    assert entry["hooks"][0]["command"] == "harness hook pre-tool-use"
    code, out = install(tmp_path, "--command", "uv run harness")
    assert out["changed"] is False
    assert (
        len(json.loads((tmp_path / ".claude/settings.json").read_text())["hooks"]["PreToolUse"])
        == 1
    )


def test_install_preserves_existing_settings(tmp_path):
    existing = {
        "permissions": {"deny": ["Read(./.env)"]},
        "hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "x"}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "y"}]}],
        },
        "model": "custom",
    }
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/settings.json").write_text(json.dumps(existing))
    code, out = install(tmp_path, "--command", "uv run harness")
    assert code == 0
    settings = json.loads((tmp_path / ".claude/settings.json").read_text())
    assert settings["permissions"] == existing["permissions"]
    assert settings["model"] == "custom"
    assert settings["hooks"]["Stop"] == existing["hooks"]["Stop"]
    assert settings["hooks"]["PreToolUse"][0] == existing["hooks"]["PreToolUse"][0]
    assert settings["hooks"]["PreToolUse"][1]["hooks"][0]["command"] == (
        "uv run harness hook pre-tool-use"
    )


def test_install_dry_run_writes_nothing(tmp_path):
    code, out = install(tmp_path, "--dry-run")
    assert code == 0 and out["changed"] is True and out["dry_run"] is True
    assert not (tmp_path / ".claude").exists()


@pytest.mark.parametrize(
    ("contents", "expected"),
    [
        ("{not json", "not valid JSON"),
        ("[]", "must contain a JSON object"),
        ('{"hooks": []}', "'hooks' must be an object"),
        ('{"hooks": {"PreToolUse": {}}}', "'hooks.PreToolUse' must be a list"),
    ],
)
def test_install_refuses_unexpected_settings(tmp_path, contents, expected):
    (tmp_path / ".claude").mkdir()
    target = tmp_path / ".claude/settings.json"
    target.write_text(contents)
    code, out = install(tmp_path)
    assert code == 1 and expected in out["errors"][0]
    assert target.read_text() == contents


def test_install_rejects_bad_command_and_symlinked_settings(tmp_path):
    code, out = install(tmp_path, "--command", " ")
    assert code == 1 and "single nonblank line" in out["errors"][0]
    (tmp_path / "real").mkdir()
    (tmp_path / ".claude").symlink_to(tmp_path / "real")
    code, out = install(tmp_path)
    assert code == 1 and "Symlinked" in out["errors"][0]
