import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from agent_harness import __version__
from agent_harness.cli import app
from agent_harness.generate import EXPECTED_FILES, MANIFEST_PATH, TEMPLATES

runner = CliRunner()


def invoke(*args):
    return runner.invoke(app, [str(arg) for arg in args])


def init(root: Path):
    result = invoke("init", root, "--name", "Demo", "--json")
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_full_workflow(tmp_path):
    root = tmp_path / "new-project"
    result = init(root)
    assert set(result["files"]) == {*EXPECTED_FILES, MANIFEST_PATH}
    for relative in result["files"]:
        assert (root / relative).is_file()
    for command in ["validate", "doctor"]:
        result = invoke(command, root, "--json")
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["ok"] is True
    config = yaml.safe_load((root / ".harness/config.yaml").read_text())
    assert config["project_name"] == "Demo"
    assert config["harness_version"] == __version__


def test_dry_run_does_not_create_directories(tmp_path):
    root = tmp_path / "missing" / "project"
    result = invoke("init", root, "--dry-run", "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["dry_run"] is True
    assert not root.parent.exists()


@pytest.mark.parametrize("relative", sorted([*EXPECTED_FILES, MANIFEST_PATH]))
def test_collision_leaves_existing_project_untouched(tmp_path, relative):
    destination = tmp_path / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("user owned\n")
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = invoke("init", tmp_path, "--json")
    assert result.exit_code == 1
    assert "overwrite" in result.output
    after = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after


def test_repeated_init_refuses_without_changing_files(tmp_path):
    init(tmp_path)
    before = (tmp_path / MANIFEST_PATH).read_bytes()
    assert invoke("init", tmp_path).exit_code == 1
    assert (tmp_path / MANIFEST_PATH).read_bytes() == before
    assert invoke("validate", tmp_path).exit_code == 0


def test_user_files_and_settings_are_preserved(tmp_path):
    (tmp_path / ".claude").mkdir()
    settings = tmp_path / ".claude/settings.json"
    settings.write_text('{"user":true}')
    app_file = tmp_path / "app.py"
    app_file.write_text("print('unchanged')\n")
    init(tmp_path)
    assert settings.read_text() == '{"user":true}'
    assert app_file.read_text() == "print('unchanged')\n"


def test_modified_files_are_warnings_not_errors(tmp_path):
    init(tmp_path)
    custom = tmp_path / "AGENTS.md"
    custom.write_text("my own rules\n")
    result = invoke("validate", tmp_path, "--json")
    report = json.loads(result.output)
    assert result.exit_code == 0, result.output
    assert report["modified_files"] == ["AGENTS.md"]
    assert report["warnings"]
    assert custom.read_text() == "my own rules\n"


@pytest.mark.parametrize("relative", sorted([*EXPECTED_FILES, MANIFEST_PATH]))
def test_missing_generated_file_is_error(tmp_path, relative):
    init(tmp_path)
    (tmp_path / relative).unlink()
    result = invoke("validate", tmp_path, "--json")
    assert result.exit_code == 1
    assert json.loads(result.output)["ok"] is False


@pytest.mark.parametrize(
    "contents", ["[broken", "[]", "null", "schema_version: 99", "!!python/object:evil {}"]
)
def test_bad_yaml_or_schema_is_rejected(tmp_path, contents):
    init(tmp_path)
    (tmp_path / ".harness/config.yaml").write_text(contents)
    result = invoke("validate", tmp_path, "--json")
    assert result.exit_code == 1
    assert "Invalid configuration" in result.output


def test_unknown_config_fields_are_rejected_without_echoing_values(tmp_path):
    init(tmp_path)
    path = tmp_path / ".harness/config.yaml"
    path.write_text(path.read_text() + "unexpected: secret-value-not-to-echo\n")
    result = invoke("validate", tmp_path, "--json")
    assert result.exit_code == 1
    assert "secret-value-not-to-echo" not in result.output


def test_manifest_cannot_redirect_validation_to_other_files(tmp_path):
    init(tmp_path)
    path = tmp_path / MANIFEST_PATH
    manifest = json.loads(path.read_text())
    manifest["files"]["../../private"] = "0" * 64
    path.write_text(json.dumps(manifest))
    result = invoke("validate", tmp_path, "--json")
    assert result.exit_code == 1
    assert "file list" in result.output


def test_broken_manifest_json_is_rejected(tmp_path):
    init(tmp_path)
    (tmp_path / MANIFEST_PATH).write_text("{")
    assert invoke("validate", tmp_path).exit_code == 1


def test_analyze_reports_commands_without_executing_them(tmp_path):
    marker = tmp_path / "must-not-exist"
    command = f"touch {marker}"
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": command}}))
    (tmp_path / "pyproject.toml").write_text('[project.scripts]\ntool = "demo:main"\n')
    result = invoke("analyze", tmp_path, "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["technologies"] == ["Python", "Node.js"]
    assert data["commands"] == {"node:test": command, "python:tool": "demo:main"}
    assert not marker.exists()
    init(tmp_path)
    config = yaml.safe_load((tmp_path / ".harness/config.yaml").read_text())
    assert config["technologies"] == ["Python", "Node.js"]


@pytest.mark.parametrize(
    "name,technology",
    [("pom.xml", "Java"), ("go.mod", "Go"), ("Cargo.toml", "Rust"), ("app.csproj", ".NET")],
)
def test_other_technology_markers(tmp_path, name, technology):
    (tmp_path / name).write_text("manifest")
    result = invoke("analyze", tmp_path, "--json")
    assert technology in json.loads(result.output)["technologies"]


def test_analysis_does_not_descend_into_dependencies(tmp_path):
    nested = tmp_path / "node_modules" / "dependency"
    nested.mkdir(parents=True)
    (nested / "package.json").write_text("{}")
    assert json.loads(invoke("analyze", tmp_path, "--json").output)["technologies"] == []


@pytest.mark.parametrize("contents", ["{", "[]", '{"scripts": []}'])
def test_bad_package_manifest_reports_warning(tmp_path, contents):
    (tmp_path / "package.json").write_text(contents)
    result = invoke("analyze", tmp_path, "--json")
    assert result.exit_code == 0
    assert json.loads(result.output)["warnings"]


def test_project_name_and_description_are_encoded_as_data(tmp_path):
    description = 'quotes: "ok"\nmultiline {{ unknown }}'
    result = invoke("init", tmp_path, "--name", "Türkçe Proje", "--description", description)
    assert result.exit_code == 0, result.output
    config = yaml.safe_load((tmp_path / ".harness/config.yaml").read_text())
    assert config["description"] == description
    assert "{{ unknown }}" in (tmp_path / "AGENTS.md").read_text()
    assert invoke("validate", tmp_path).exit_code == 0


@pytest.mark.parametrize("name", ["", "  ", "line\nbreak"])
def test_invalid_names_leave_no_files(tmp_path, name):
    result = invoke("init", tmp_path, "--name", name)
    assert result.exit_code == 1
    assert list(tmp_path.iterdir()) == []


def test_agent_and_skill_frontmatter_is_valid(tmp_path):
    init(tmp_path)
    for relative in TEMPLATES:
        if relative.startswith(".claude/agents/") or relative.endswith("SKILL.md"):
            contents = (tmp_path / relative).read_text()
            metadata = yaml.safe_load(contents.split("---", 2)[1])
            assert metadata["name"]
            assert metadata["description"]
    assert "@AGENTS.md" in (tmp_path / "CLAUDE.md").read_text()


def test_version_and_help():
    assert invoke("--version").output.strip() == __version__
    result = invoke("--help")
    assert result.exit_code == 0
    for command in ["init", "analyze", "validate", "doctor"]:
        assert command in result.output


def test_missing_project_fails_cleanly(tmp_path):
    for command in ["analyze", "validate", "doctor"]:
        result = invoke(command, tmp_path / "missing", "--json")
        assert result.exit_code == 1
        assert "Traceback" not in result.output
        assert json.loads(result.output)["ok"] is False


def test_default_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert invoke("init").exit_code == 0
    assert invoke("validate").exit_code == 0
