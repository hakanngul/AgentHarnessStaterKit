"""Local development prerequisites; no remote calls or interactive authentication."""

import shutil
import sys
from pathlib import Path

from agent_harness.models import Report
from agent_harness.validate import validate


def doctor(path: Path) -> Report:
    report = validate(path)
    report.checks.append(
        f"Python {sys.version_info.major}.{sys.version_info.minor} is available (requires 3.11+)"
    )
    if shutil.which("git"):
        report.checks.append("Git is available")
    else:
        report.errors.append("Git is missing; install Git to use the development workflow")
    if shutil.which("claude"):
        report.checks.append("Claude CLI is on PATH; authentication/runtime was not tested")
    else:
        report.warnings.append(
            "Claude CLI is not on PATH; optional for setup, required to run Claude Code agents"
        )
    return report
