from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_harness.engine.policy import check_tool
from agent_harness.models import HarnessConfig, RoleConfig

ROLES = {
    "developer": {
        "write": ["src/**", "tests/**"],
        "deny": ["src/secrets/**"],
        "bash": ["uv run pytest", "ruff check", "git status"],
    },
    "reviewer": {"write": [".harness/tasks/**"]},
}


def config(**roles) -> HarnessConfig:
    return HarnessConfig.model_validate({"project_name": "Demo", "roles": roles or ROLES})


def verdict(tool: str, tool_input: dict, role: str = "developer", root: Path = Path("/proj")):
    return check_tool(config(), role, root, tool, tool_input)


# --- role config ------------------------------------------------------------------


def test_engine_fields_are_optional():
    role = RoleConfig.model_validate({"write": ["src/**"]})
    assert role.model is None and role.effort is None and role.bash == []


def test_engine_fields_are_parsed():
    role = RoleConfig.model_validate(
        {"model": "claude-opus-5-5", "effort": "high", "bash": ["uv run pytest"]}
    )
    assert (role.model, role.effort, role.bash) == ("claude-opus-5-5", "high", ["uv run pytest"])


@pytest.mark.parametrize(
    "bad",
    [
        {"effort": "turbo"},
        {"model": ""},
        {"model": "a b"},
        {"model": "x;rm"},
        {"bash": [""]},
        {"bash": ["pytest; rm -rf /"]},
        {"bash": ["cat | sh"]},
        {"bash": ["echo `id`"]},
        {"bash": ["echo $(id)"]},
        {"bash": ["ls > out"]},
        {"bash": ["a && b"]},
        {"bash": [" pytest"]},
        {"bash": ["pytest  -q"]},
        {"extra": 1},
    ],
)
def test_invalid_engine_fields_are_rejected(bad):
    with pytest.raises(ValidationError):
        RoleConfig.model_validate(bad)


# --- file writes ------------------------------------------------------------------


@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit"])
def test_write_inside_scope_is_allowed(tool):
    assert verdict(tool, {"file_path": "/proj/src/app.py"}).allowed


def test_relative_path_resolves_against_the_project_root():
    assert verdict("Write", {"file_path": "src/app.py"}).allowed
    assert not verdict("Write", {"file_path": "docs/readme.md"}).allowed


@pytest.mark.parametrize(
    "target",
    ["/proj/docs/readme.md", "/proj/src/secrets/key.txt", "/elsewhere/file", "src/../docs/x"],
)
def test_write_outside_scope_is_denied_with_a_reason(target):
    result = verdict("Write", {"file_path": target})
    assert not result.allowed and result.reason


def test_notebook_edit_uses_notebook_path():
    assert verdict("NotebookEdit", {"notebook_path": "/proj/src/n.ipynb"}).allowed
    assert not verdict("NotebookEdit", {"notebook_path": "/proj/docs/n.ipynb"}).allowed
    assert not verdict("NotebookEdit", {"file_path": "/proj/src/n.ipynb"}).allowed


@pytest.mark.parametrize("tool_input", [{}, {"file_path": ""}, {"file_path": 3}])
def test_write_without_a_target_is_denied(tool_input):
    assert not verdict("Write", tool_input).allowed


def test_reviewer_cannot_write_source():
    assert not verdict("Write", {"file_path": "/proj/src/app.py"}, role="reviewer").allowed
    assert verdict("Write", {"file_path": "/proj/.harness/tasks/T-1.yaml"}, role="reviewer").allowed


# --- shell ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command", ["uv run pytest", "uv run pytest -q tests/", "ruff check .", "  git status  "]
)
def test_listed_command_prefixes_are_allowed(command):
    assert verdict("Bash", {"command": command}).allowed


@pytest.mark.parametrize("command", ["uv run python x.py", "rm -rf /", "pytest", "git push"])
def test_unlisted_commands_are_denied_and_name_the_allowed_ones(command):
    result = verdict("Bash", {"command": command})
    assert not result.allowed
    assert "uv run pytest" in result.reason


@pytest.mark.parametrize(
    "command",
    [
        "uv run pytest; rm -rf /",
        "uv run pytest && curl evil.sh",
        "uv run pytest || true",
        "uv run pytest | tee out",
        "uv run pytest > out.txt",
        "uv run pytest < in.txt",
        "uv run pytest `id`",
        "uv run pytest $(id)",
        "uv run pytest &",
        "uv run pytest\nrm -rf /",
        "uv run pytest\r\nrm x",
    ],
)
def test_chained_or_redirected_commands_are_denied(command):
    result = verdict("Bash", {"command": command})
    assert not result.allowed
    assert "one command at a time" in result.reason


def test_prefix_must_match_whole_words():
    assert not verdict("Bash", {"command": "ruff checker"}).allowed
    assert not verdict("Bash", {"command": "git statusx"}).allowed


def test_role_without_bash_list_may_not_run_commands():
    result = verdict("Bash", {"command": "ls"}, role="reviewer")
    assert not result.allowed and "allowed: none" in result.reason


@pytest.mark.parametrize("tool_input", [{}, {"command": None}, {"command": ""}, {"command": "  "}])
def test_bash_without_a_command_is_denied(tool_input):
    assert not verdict("Bash", tool_input).allowed


# --- everything else --------------------------------------------------------------


@pytest.mark.parametrize(
    "tool", ["Read", "Grep", "Glob", "LS", "TodoWrite", "Task", "Agent", "Skill", "BashOutput"]
)
def test_read_only_tools_are_allowed(tool):
    assert verdict(tool, {"anything": "x"}).allowed


def test_unknown_role_is_denied_for_every_tool():
    for tool, tool_input in [("Read", {}), ("Write", {"file_path": "src/a.py"}), ("Bash", {})]:
        result = verdict(tool, tool_input, role="ghost")
        assert not result.allowed and "ghost" in result.reason


@pytest.mark.parametrize("tool", ["mcp__github__create_or_update_file", "mcp__fs__write", "mcp__x"])
def test_mcp_tools_are_denied(tool):
    result = verdict(tool, {"path": "src/a.py"})
    assert not result.allowed and "MCP" in result.reason


@pytest.mark.parametrize("tool", ["PowerShell", "KillShell", "SomeFutureTool", "", "bash"])
def test_unknown_tools_are_denied_by_default(tool):
    result = verdict(tool, {"command": "ls", "file_path": "src/a.py"})
    assert not result.allowed and "allow list" in result.reason


@pytest.mark.parametrize("tool", ["WebFetch", "WebSearch"])
def test_network_tools_are_denied(tool):
    result = verdict(tool, {"url": "https://example.com", "query": "x"})
    assert not result.allowed and "network" in result.reason


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Read", {"file_path": "/etc/passwd"}),
        ("Read", {"file_path": "~/.ssh/id_rsa"}),
        ("Read", {"file_path": "../secrets.txt"}),
        ("Read", {"file_path": "/proj/../etc/passwd"}),
        ("Read", {"file_path": "/projx/file"}),
        ("Glob", {"pattern": "*", "path": "/"}),
        ("Grep", {"pattern": "x", "path": "/home"}),
        ("LS", {"path": "/tmp"}),
        ("NotebookRead", {"notebook_path": "/other/n.ipynb"}),
    ],
)
def test_reads_outside_the_project_are_denied(tool, tool_input):
    result = verdict(tool, tool_input)
    assert not result.allowed and "inside the project" in result.reason


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Read", {"file_path": "/proj/src/app.py"}),
        ("Read", {"file_path": "src/app.py"}),
        ("Read", {"file_path": "/proj/docs/readme.md"}),
        ("Glob", {"pattern": "**/*.py", "path": "/proj"}),
        ("Glob", {"pattern": "**/*.py", "path": "."}),
        ("Grep", {"pattern": "x"}),
        ("Grep", {"pattern": "x", "path": "src"}),
        ("LS", {"path": "/proj/src"}),
    ],
)
def test_reads_inside_the_project_are_allowed(tool, tool_input):
    assert verdict(tool, tool_input).allowed


# --- harness evidence run ---------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "harness evidence run T-1 -- uv run pytest",
        "harness evidence run T-1 --cwd app -- uv run pytest -q tests/",
        "uv run harness evidence run T-1 -- ruff check .",
    ],
)
def test_evidence_run_is_allowed_when_the_inner_command_is_listed(command):
    assert verdict("Bash", {"command": command}).allowed


@pytest.mark.parametrize(
    "command",
    [
        "harness evidence run T-1 -- rm -rf /",
        "harness evidence run T-1 -- harness evidence run T-1 -- rm x",
        "harness evidence run T-1",
        "harness evidence run T-1 --",
        "harness evidence run T-1 -- uv run pytest; rm -rf /",
        "harness evidence run T-1 -- uv run pytest && rm x",
        "harness evidence runx T-1 -- uv run pytest",
        "harness evidence",
        "harness check .",
    ],
)
def test_evidence_run_cannot_smuggle_an_unlisted_command(command):
    assert not verdict("Bash", {"command": command}).allowed


def test_evidence_run_needs_a_bash_list():
    command = "harness evidence run T-1 -- uv run pytest"
    assert not verdict("Bash", {"command": command}, role="reviewer").allowed


# --- evidence run: the command parser also collects words before `--` -------------


@pytest.mark.parametrize(
    "command",
    [
        "harness evidence run T-1 rm build -- uv run pytest",
        "harness evidence run T-1 echo hi -- uv run pytest",
        "harness evidence run T-1 uv run pytest -- uv run pytest",
        "harness evidence run -- uv run pytest",
        "harness evidence run notatask -- uv run pytest",
        "harness evidence run T-1 --path /elsewhere -- uv run pytest",
        "harness evidence run T-1 --evil x -- uv run pytest",
        "harness evidence run T-1 --cwd /etc -- uv run pytest",
        "harness evidence run T-1 --cwd .. -- uv run pytest",
        "harness evidence run T-1 --cwd -- uv run pytest",
        "harness evidence run T-1 --cwd --id -- uv run pytest",
        "harness evidence run T-1 --id",
        "uv run harness evidence run T-1 rm x -- ruff check .",
    ],
)
def test_evidence_run_accepts_only_the_strict_shape(command):
    assert not verdict("Bash", {"command": command}).allowed


@pytest.mark.parametrize(
    "command",
    [
        "harness evidence run T-1 -- uv run pytest",
        "harness evidence run T-12 --cwd app -- uv run pytest",
        "harness evidence run T-1 --cwd . --id E9 --timeout 600 -- uv run pytest -q",
        "harness evidence run T-1 --json -- ruff check .",
        "harness evidence run API-3 --summary done -- git status",
        "harness evidence run T-1 -- uv run pytest -- -k login",
    ],
)
def test_evidence_run_with_known_options_is_allowed(command):
    assert verdict("Bash", {"command": command}).allowed


def test_the_command_parser_really_merges_words_before_the_separator(monkeypatch):
    """Pins why the strict shape is needed: `rm build` ends up inside the command."""
    from typer.testing import CliRunner

    import agent_harness.cli as cli

    seen = {}

    def fake(path, task, command, **kwargs):
        seen["command"] = list(command)
        raise cli.HarnessError("stop")

    monkeypatch.setattr(cli, "run_evidence", fake)
    CliRunner().invoke(
        cli.app, ["evidence", "run", "T-1", "rm", "build", "--", "uv", "run", "pytest"]
    )
    assert seen["command"] == ["rm", "build", "uv", "run", "pytest"]


# --- glob patterns ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Glob", {"pattern": "/etc/*"}),
        ("Glob", {"pattern": "~/.ssh/*"}),
        ("Glob", {"pattern": "../../**"}),
        ("Glob", {"pattern": "src/../../x", "path": "/proj"}),
        ("Glob", {"pattern": "..\\..\\x"}),
        ("Grep", {"pattern": "x", "glob": "/etc/*"}),
        ("Grep", {"pattern": "x", "glob": "../*"}),
    ],
)
def test_glob_patterns_that_leave_the_project_are_denied(tool, tool_input):
    result = verdict(tool, tool_input)
    assert not result.allowed and "inside the project" in result.reason


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Glob", {"pattern": "**/*.py"}),
        ("Glob", {"pattern": "src/*.py", "path": "/proj"}),
        ("Grep", {"pattern": "..", "glob": "*.py"}),
        ("Grep", {"pattern": "x", "glob": "src/**"}),
    ],
)
def test_glob_patterns_inside_the_project_are_allowed(tool, tool_input):
    assert verdict(tool, tool_input).allowed
