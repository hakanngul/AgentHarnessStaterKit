"""Inspect root manifests without executing project commands or scanning dependencies."""

import json
import tomllib
from pathlib import Path

from agent_harness.filesystem import HarnessError, managed_path, project_root, read_text
from agent_harness.models import Analysis

MARKERS = {
    "Python": ("pyproject.toml", "requirements.txt", "setup.py", "Pipfile"),
    "Node.js": ("package.json",),
    "Java": ("pom.xml", "build.gradle", "build.gradle.kts"),
    "Go": ("go.mod",),
    "Rust": ("Cargo.toml",),
    ".NET": ("global.json",),
}


def analyze(path: Path) -> Analysis:
    root = project_root(path)
    result = Analysis(root=str(root))
    for technology, names in MARKERS.items():
        for name in names:
            try:
                candidate = managed_path(root, name)
                if candidate.exists():
                    read_text(candidate)
                    result.manifests.append(name)
                    if technology not in result.technologies:
                        result.technologies.append(technology)
            except (HarnessError, OSError, UnicodeError) as exc:
                result.warnings.append(str(exc))
    # Only direct project files; never descend into vendor/node_modules or secret files.
    for item in sorted(root.iterdir()):
        if (
            item.suffix in {".csproj", ".fsproj", ".sln"}
            and not item.is_symlink()
            and item.is_file()
        ):
            result.manifests.append(item.name)
            if ".NET" not in result.technologies:
                result.technologies.append(".NET")
    if "package.json" in result.manifests:
        try:
            package = json.loads(read_text(root / "package.json"))
            scripts = package.get("scripts", {})
            if not isinstance(scripts, dict):
                raise ValueError("scripts must be an object")
            result.commands.update(
                {f"node:{k}": v for k, v in scripts.items() if isinstance(v, str)}
            )
        except (ValueError, AttributeError, OSError, UnicodeError, HarnessError) as exc:
            result.warnings.append(f"Cannot parse package.json: {exc}")
    if "pyproject.toml" in result.manifests:
        try:
            data = tomllib.loads(read_text(root / "pyproject.toml"))
            scripts = data.get("project", {}).get("scripts", {})
            if not isinstance(scripts, dict):
                raise ValueError("project.scripts must be a table")
            result.commands.update(
                {f"python:{k}": v for k, v in scripts.items() if isinstance(v, str)}
            )
        except (ValueError, AttributeError, OSError, UnicodeError, HarnessError) as exc:
            result.warnings.append(f"Cannot parse pyproject.toml: {exc}")
    return result
