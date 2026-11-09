"""Claude Code PreToolUse hook that applies role write scopes, and its installer.

The hook is a guardrail, not a sandbox: it only sees file-editing tools, so writes
through Bash are not covered. CI (`harness check`) remains the real gate.

Role: the hook input's `agent_type` when that name is a configured role, otherwise the
HARNESS_ROLE environment variable. With no role, no configured roles or unreadable
input the hook does not block and says why on stderr; input too large to inspect is
denied when roles are configured.
"""

import json
import os
from pathlib import Path
from typing import Any, TextIO

from agent_harness.filesystem import (
    HarnessError,
    atomic_write,
    managed_path,
    project_root,
    read_text,
)
from agent_harness.roles import CONFIG_PATH, decide, load_config

MAX_INPUT_CHARS = 64 * 1_048_576
SETTINGS_PATH = ".claude/settings.json"
MATCHER = "Write|Edit|MultiEdit|NotebookEdit"
HOOK_SUFFIX = "hook pre-tool-use"


def _deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _project(directory: str) -> tuple[Path, tuple[Path, ...], Any]:
    """Real project root, its other spelling (if symlinked) and the config, or raise."""
    raw = Path(os.path.abspath(os.path.expanduser(directory)))
    root = project_root(Path(os.path.realpath(raw)))
    aliases = (raw,) if raw != root else ()
    if not managed_path(root, CONFIG_PATH).exists():
        return root, aliases, None
    return root, aliases, load_config(root)


def evaluate(payload: dict[str, Any], environ: dict[str, str]) -> tuple[dict | None, str]:
    """Return (stdout JSON or None to allow, stderr note)."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None, ""
    target = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not isinstance(target, str) or not target:
        return None, ""
    project = environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd")
    if not isinstance(project, str) or not project:
        return None, "harness hook: no project directory; not enforcing"
    try:
        root, aliases, config = _project(project)
    except (HarnessError, OSError) as exc:
        return None, f"harness hook: {exc}; not enforcing"
    if config is None or not config.roles:
        return None, ""

    # The runtime's agent name is more specific than a session-wide HARNESS_ROLE,
    # so a configured agent_type (a subagent or `claude --agent`) wins.
    agent = payload.get("agent_type")
    role = agent if isinstance(agent, str) and agent in config.roles else ""
    role = role or environ.get("HARNESS_ROLE", "").strip()
    if not role:
        return None, "harness hook: role unknown (set HARNESS_ROLE); not enforcing"
    if role not in config.roles:
        known = ", ".join(sorted(config.roles))
        return _deny(f"[harness] Role {role!r} is not defined in {CONFIG_PATH} ({known})."), ""

    cwd = payload.get("cwd")
    base = Path(cwd) if isinstance(cwd, str) and cwd else root
    decision = decide(config, role, root, target, base, aliases)
    if decision.allowed:
        return None, ""
    return _deny(
        f"[harness] Role {role!r} may not write {decision.path}: {decision.reason}. "
        f"Write scopes are in {CONFIG_PATH}; ask the lead or the user if this is needed."
    ), ""


def _oversized(environ: dict[str, str]) -> tuple[dict | None, str]:
    """Input too large to inspect: fail closed when the project defines roles."""
    project = environ.get("CLAUDE_PROJECT_DIR")
    try:
        config = _project(project)[2] if project else None
    except (HarnessError, OSError):
        config = None
    if config is not None and config.roles:
        return _deny(
            f"[harness] Tool input exceeds {MAX_INPUT_CHARS} characters and cannot be "
            "checked against write scopes; split the change into smaller edits."
        ), ""
    return None, "harness hook: input too large; not enforcing"


def run_pre_tool_use(stdin: TextIO, stdout: TextIO, stderr: TextIO, environ=None) -> None:
    """Hook entry point. Always exits 0: Claude Code reads a denial from stdout JSON."""
    environ = dict(os.environ if environ is None else environ)
    try:
        raw = stdin.read(MAX_INPUT_CHARS + 1)
        if len(raw) > MAX_INPUT_CHARS:
            output, note = _oversized(environ)
        else:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                output, note = evaluate(payload, environ)
            else:
                output, note = None, "harness hook: input is not a JSON object; not enforcing"
    except (ValueError, RecursionError) as exc:  # bad UTF-8, invalid or too deep JSON
        output, note = None, f"harness hook: unreadable input ({type(exc).__name__}); not enforcing"
    if note:
        print(note, file=stderr)
    if output is not None:
        stdout.write(json.dumps(output, ensure_ascii=False) + "\n")


def _is_ours(hook: Any) -> bool:
    return (
        isinstance(hook, dict)
        and isinstance(hook.get("command"), str)
        and hook["command"].rstrip().endswith(HOOK_SUFFIX)
    )


def install(path: Path, *, command: str = "harness", dry_run: bool = False) -> dict[str, Any]:
    """Add the PreToolUse hook to .claude/settings.json, preserving existing settings."""
    if not command.strip() or "\n" in command:
        raise HarnessError("Hook command must be a single nonblank line")
    root = project_root(path)
    target = managed_path(root, SETTINGS_PATH)
    settings: dict[str, Any] = {}
    if target.exists():
        try:
            settings = json.loads(read_text(target))
        except ValueError as exc:
            raise HarnessError(f"{SETTINGS_PATH} is not valid JSON: {exc}") from exc
        if not isinstance(settings, dict):
            raise HarnessError(f"{SETTINGS_PATH} must contain a JSON object")
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise HarnessError(f"{SETTINGS_PATH}: 'hooks' must be an object")
    entries = hooks.setdefault("PreToolUse", [])
    if not isinstance(entries, list):
        raise HarnessError(f"{SETTINGS_PATH}: 'hooks.PreToolUse' must be a list")
    for entry in entries:
        if isinstance(entry, dict) and any(_is_ours(h) for h in entry.get("hooks") or []):
            return {"changed": False, "path": SETTINGS_PATH, "settings": settings}
    entries.append(
        {
            "matcher": MATCHER,
            "hooks": [
                {"type": "command", "command": f"{command.strip()} {HOOK_SUFFIX}", "timeout": 30}
            ],
        }
    )
    if not dry_run:
        if not target.parent.exists():
            target.parent.mkdir()
        atomic_write(target, json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
    return {"changed": True, "dry_run": dry_run, "path": SETTINGS_PATH, "settings": settings}
