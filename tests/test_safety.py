import json
from pathlib import Path

import pytest

from agent_harness.doctor import doctor
from agent_harness.filesystem import HarnessError, managed_path
from agent_harness.generate import initialize
from agent_harness.validate import validate


@pytest.mark.parametrize(
    "relative", ["../outside", "/outside", "a/../../outside", "a\\outside", "C:/x", "a//b", "./a"]
)
def test_unsafe_managed_paths_are_rejected(tmp_path, relative):
    with pytest.raises(HarnessError, match="Unsafe"):
        managed_path(tmp_path, relative)


@pytest.mark.parametrize("relative", ["AGENTS.md", ".claude", ".harness", "docs"])
def test_init_rejects_symlink_destinations(tmp_path, relative):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    (root / relative).symlink_to(outside, target_is_directory=True)
    with pytest.raises(HarnessError, match="Symlink"):
        initialize(root)
    assert list(outside.iterdir()) == []
    assert len(list(root.iterdir())) == 1


def test_root_symlink_is_rejected(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    root = tmp_path / "link"
    root.symlink_to(actual, target_is_directory=True)
    with pytest.raises(HarnessError, match="Symlink"):
        initialize(root)
    assert not validate(root).ok
    assert list(actual.iterdir()) == []


def test_validate_rejects_replaced_file_symlink(tmp_path):
    root = tmp_path / "project"
    initialize(root)
    source = tmp_path / "external"
    source.write_text("private data")
    target = root / "AGENTS.md"
    target.unlink()
    target.symlink_to(source)
    report = validate(root)
    assert not report.ok
    assert any("Symlink" in error for error in report.errors)
    assert source.read_text() == "private data"


def test_dangling_symlink_is_rejected(tmp_path):
    (tmp_path / "AGENTS.md").symlink_to(tmp_path / "missing")
    with pytest.raises(HarnessError, match="Symlink"):
        initialize(tmp_path)


def test_parent_file_is_rejected_before_writes(tmp_path):
    (tmp_path / ".claude").write_text("user file")
    with pytest.raises(HarnessError, match="not a directory"):
        initialize(tmp_path)
    assert not (tmp_path / "AGENTS.md").exists()


def test_write_failure_rolls_back_only_generated_files(tmp_path, monkeypatch):
    owned = tmp_path / "user.txt"
    owned.write_text("keep me")
    original_open = Path.open

    def fail_later(path, *args, **kwargs):
        if path.name == "developer.md" and args and args[0] == "x":
            raise OSError("simulated disk failure")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_later)
    with pytest.raises(OSError, match="simulated"):
        initialize(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["user.txt"]
    assert owned.read_text() == "keep me"


def test_large_config_is_rejected(tmp_path):
    initialize(tmp_path)
    (tmp_path / ".harness/config.yaml").write_bytes(b"x" * 1_048_577)
    report = validate(tmp_path)
    assert not report.ok
    assert any("limit" in error for error in report.errors)


def test_bad_hash_is_rejected(tmp_path):
    initialize(tmp_path)
    path = tmp_path / ".harness/manifest.json"
    manifest = json.loads(path.read_text())
    manifest["files"]["AGENTS.md"] = "not a sha256"
    path.write_text(json.dumps(manifest))
    assert not validate(tmp_path).ok


def test_missing_claude_is_optional_but_git_is_required(tmp_path, monkeypatch):
    initialize(tmp_path)
    monkeypatch.setattr("agent_harness.doctor.shutil.which", lambda name: None)
    report = doctor(tmp_path)
    assert not report.ok
    assert any("Git is missing" in error for error in report.errors)
    assert any("Claude CLI" in warning for warning in report.warnings)
    monkeypatch.setattr(
        "agent_harness.doctor.shutil.which", lambda name: "/usr/bin/git" if name == "git" else None
    )
    assert doctor(tmp_path).ok


@pytest.mark.parametrize("version", [True, 1.0])
@pytest.mark.parametrize("relative", [".harness/config.yaml", ".harness/manifest.json"])
def test_schema_version_must_be_an_integer(tmp_path, version, relative):
    import yaml

    initialize(tmp_path)
    path = tmp_path / relative
    if relative.endswith(".json"):
        data = json.loads(path.read_text())
        data["schema_version"] = version
        path.write_text(json.dumps(data))
    else:
        data = yaml.safe_load(path.read_text())
        data["schema_version"] = version
        path.write_text(yaml.safe_dump(data))
    assert not validate(tmp_path).ok


def test_line_ending_changes_are_detected(tmp_path):
    initialize(tmp_path)
    path = tmp_path / "AGENTS.md"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert "AGENTS.md" in validate(tmp_path).modified_files
