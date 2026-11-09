import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from agent_harness.check import TASKS_DIR
from agent_harness.cli import app
from agent_harness.models import HarnessConfig
from agent_harness.roles import glob_match

runner = CliRunner()


def write_config(root: Path, roles: dict | None = None, **extra) -> None:
    data = {"project_name": "Demo", **extra}
    if roles is not None:
        data["roles"] = roles
    target = root / ".harness/config.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(data))


ROLES = {
    "developer": {"write": ["src/**", "tests/", "README.md"], "deny": ["src/secrets/**"]},
    "reviewer": {"write": [".harness/tasks/*.yaml"]},
}


def scope(root: Path, role: str, *paths: str):
    result = runner.invoke(app, ["scope", role, *paths, "--path", str(root), "--json"])
    return result.exit_code, json.loads(result.output)


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("src/**", "src/a.py", True),
        ("src/**", "src/a/b/c.py", True),
        ("src/**", "srcx/a.py", False),
        ("src/**", "src", False),
        ("tests/", "tests/unit/test_a.py", True),
        ("*.md", "README.md", True),
        ("*.md", "docs/README.md", False),
        ("**/*.md", "README.md", True),
        ("**/*.md", "docs/a/README.md", True),
        ("src/**/test_*.py", "src/test_a.py", True),
        ("src/**/test_*.py", "src/x/y/test_a.py", True),
        ("src/**/test_*.py", "src/x/a.py", False),
        ("a?.txt", "ab.txt", True),
        ("a?.txt", "a/.txt", False),
        ("**", "anything/at/all", True),
        ("a.b", "axb", False),
        ("src/(x)+", "src/(x)+", True),
    ],
)
def test_glob_match(pattern, path, expected):
    assert glob_match(pattern, path) is expected


@pytest.mark.parametrize(
    "glob",
    ["/etc/**", "../x", "a/../b", "a\\b", "C:/x", "a//b", "a[bc]", "src/**x", "", "./.harness/**"],
)
def test_unsafe_or_unsupported_globs_are_rejected(glob):
    with pytest.raises(ValueError):
        HarnessConfig.model_validate(
            {"project_name": "x", "roles": {"developer": {"write": [glob]}}}
        )


@pytest.mark.parametrize("name", ["Developer", "1dev", "dev_ops", "", "a" * 41])
def test_role_names_are_validated(name):
    with pytest.raises(ValueError):
        HarnessConfig.model_validate({"project_name": "x", "roles": {name: {}}})


def test_unknown_role_fields_are_rejected():
    with pytest.raises(ValueError):
        HarnessConfig.model_validate(
            {"project_name": "x", "roles": {"developer": {"writes": ["**"]}}}
        )


def test_scope_allow_deny_and_default(tmp_path):
    write_config(tmp_path, ROLES)
    code, out = scope(tmp_path, "developer", "src/app.py", "tests/x.py", "README.md")
    assert code == 0, out
    assert all(d["allowed"] for d in out["decisions"])
    code, out = scope(tmp_path, "developer", "src/secrets/key.txt")
    assert code == 1
    assert out["decisions"][0]["reason"] == "denied by 'src/secrets/**'"
    code, out = scope(tmp_path, "developer", "docs/a.md")
    assert code == 1 and "not in the role's write scope" in out["decisions"][0]["reason"]


@pytest.mark.parametrize(
    ("target", "reason"),
    [
        ("../outside.py", "'..' segments"),
        ("/etc/passwd", "outside the project"),
        ("src/../../x", "'..' segments"),
        (".", "project root"),
    ],
)
def test_scope_rejects_escapes(tmp_path, target, reason):
    write_config(tmp_path, {"developer": {"write": ["**"]}})
    code, out = scope(tmp_path, "developer", target)
    assert code == 1
    assert reason in out["decisions"][0]["reason"]


def test_scope_rejects_dotdot_even_inside_project(tmp_path):
    write_config(tmp_path, ROLES)
    code, out = scope(tmp_path, "developer", "./src/../src/app.py")
    assert code == 1 and "'..' segments" in out["decisions"][0]["reason"]
    code, out = scope(tmp_path, "developer", "./src/./app.py")
    assert code == 0 and out["decisions"][0]["path"] == "src/app.py"


def test_scope_rejects_symlinked_directories(tmp_path):
    write_config(tmp_path, {"developer": {"write": ["**"]}})
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "src").symlink_to(tmp_path / "elsewhere")
    code, out = scope(tmp_path, "developer", "src/a.py")
    assert code == 1 and "Symlinked" in out["decisions"][0]["reason"]


def test_scope_absolute_path_inside_project(tmp_path):
    write_config(tmp_path, ROLES)
    code, out = scope(tmp_path, "developer", str(tmp_path / "src" / "a.py"))
    assert code == 0 and out["decisions"][0]["path"] == "src/a.py"


def test_scope_unknown_role_and_missing_roles(tmp_path):
    write_config(tmp_path, ROLES)
    code, out = scope(tmp_path, "qa", "src/a.py")
    assert code == 1 and "unknown role 'qa'" in out["decisions"][0]["reason"]
    write_config(tmp_path)
    code, out = scope(tmp_path, "developer", "src/a.py")
    assert code == 1 and "No roles are defined" in out["errors"][0]


def test_scope_invalid_config(tmp_path):
    write_config(tmp_path, {"developer": {"write": ["../x"]}})
    code, out = scope(tmp_path, "developer", "a")
    assert code == 1 and "Invalid configuration" in out["errors"][0]


def test_config_without_roles_still_validates(tmp_path):
    assert HarnessConfig.model_validate({"project_name": "x"}).roles == {}


def test_check_requires_owner_to_be_a_configured_role(tmp_path):
    write_config(tmp_path, ROLES)
    task = tmp_path / TASKS_DIR / "T-1.yaml"
    task.parent.mkdir(parents=True)
    task.write_text("id: T-1\ntitle: x\nowner: qa\nstatus: BACKLOG\n")
    result = runner.invoke(app, ["check", str(tmp_path), "--json"])
    assert result.exit_code == 1
    assert "owner 'qa' is not a role" in result.output
    task.write_text("id: T-1\ntitle: x\nowner: developer\nstatus: BACKLOG\n")
    assert runner.invoke(app, ["check", str(tmp_path)]).exit_code == 0


def test_check_without_roles_accepts_any_owner(tmp_path):
    write_config(tmp_path)
    task = tmp_path / TASKS_DIR / "T-1.yaml"
    task.parent.mkdir(parents=True)
    task.write_text("id: T-1\ntitle: x\nowner: anyone\nstatus: BACKLOG\n")
    assert runner.invoke(app, ["check", str(tmp_path)]).exit_code == 0


def test_check_reports_invalid_config(tmp_path):
    write_config(tmp_path, schema_version=9)
    result = runner.invoke(app, ["check", str(tmp_path), "--json"])
    assert result.exit_code == 1
    assert "config.yaml" in result.output


def test_init_writes_default_roles_that_scope_uses(tmp_path):
    assert runner.invoke(app, ["init", str(tmp_path), "--name", "Demo"]).exit_code == 0
    config = yaml.safe_load((tmp_path / ".harness/config.yaml").read_text())
    assert set(config["roles"]) == {"lead", "developer", "reviewer"}
    assert runner.invoke(app, ["validate", str(tmp_path)]).exit_code == 0
    code, out = scope(tmp_path, "developer", "src/a.py", ".claude/agents/lead.md")
    assert [d["allowed"] for d in out["decisions"]] == [True, False]
    code, out = scope(tmp_path, "lead", "src/a.py", ".harness/tasks/T-1.yaml")
    assert [d["allowed"] for d in out["decisions"]] == [False, True]
