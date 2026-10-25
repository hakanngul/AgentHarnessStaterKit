"""Validate schema and required files; report customizations without overwriting them."""

from pathlib import Path

import yaml
from pydantic import ValidationError

from agent_harness.filesystem import HarnessError, managed_path, project_root, read_text
from agent_harness.generate import CONFIG_PATH, EXPECTED_FILES, MANIFEST_PATH, sha256
from agent_harness.models import HarnessConfig, Manifest, Report


def validate(path: Path) -> Report:
    report = Report()
    try:
        root = project_root(path)
    except HarnessError as exc:
        report.errors.append(str(exc))
        return report
    try:
        raw = read_text(managed_path(root, CONFIG_PATH))
        HarnessConfig.model_validate(yaml.safe_load(raw))
        report.checks.append("Configuration schema is valid")
    except (HarnessError, OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
        message = (
            str(exc)
            if not isinstance(exc, ValidationError)
            else str(exc.errors(include_input=False))
        )
        report.errors.append(f"Invalid configuration: {message}")
    try:
        manifest = Manifest.model_validate_json(read_text(managed_path(root, MANIFEST_PATH)))
        if set(manifest.files) != EXPECTED_FILES:
            raise HarnessError(
                "Manifest file list does not match the supported installation layout"
            )
        report.checks.append("Manifest schema and file list are valid")
    except (HarnessError, OSError, UnicodeError, ValueError) as exc:
        message = (
            str(exc)
            if not isinstance(exc, ValidationError)
            else str(exc.errors(include_input=False))
        )
        report.errors.append(f"Invalid manifest: {message}")
        manifest = None
    for relative in sorted(EXPECTED_FILES):
        try:
            raw = read_text(managed_path(root, relative))
            if manifest and sha256(raw.encode()) != manifest.files[relative]:
                report.modified_files.append(relative)
        except (HarnessError, OSError, UnicodeError) as exc:
            report.errors.append(str(exc))
    if report.modified_files:
        report.warnings.append("Local customizations detected; files were preserved")
    return report
