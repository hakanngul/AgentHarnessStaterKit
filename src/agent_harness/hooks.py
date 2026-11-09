"""Claude Code PreToolUse hook that applies role write scopes, and its installer.

The hook is a guardrail, not a sandbox: it only sees file-editing tools, so writes
through Bash are not covered. CI (`harness check`) remains the real gate.

Role: the HARNESS_ROLE environment variable, otherwise the hook input's `agent_type`
when that name is a configured role. With no role, no configured roles or unreadable
input, the hook does not block and says why on stderr.
"""

import json
import os
from pathlib import Path
from typing import Any, TextIO

from agent_harness.filesystem import HarnessError, managed_path, project_root, read_text
from agent_harness.roles import CONFIG_PATH, decide, load_config

MAX_INPUT_BYTES = 1_048_576
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
        root = project_root(Path(project))
        if not managed_path(root, CONFIG_PATH).exists():
            return None, ""
        config = load_config(root)
    except (HarnessError, OSError) as exc:
        return None, f"harness hook: {exc}; not enforcing"
    if not config.roles:
        return None, ""

    role = environ.get("HARNESS_ROLE", "").strip()
    if not role:
        agent = payload.get("agent_type")
        role = agent if isinstance(agent, str) and agent in config.roles else ""
    if not role:
        return None, "harness hook: role unknown (set HARNESS_ROLE); not enforcing"
    if role not in config.roles:
        known = ", ".join(sorted(config.roles))
        return _deny(f"[harness] Role {role!r} is not defined in {CONFIG_PATH} ({known})."), ""

    cwd = payload.get("cwd")
    base = Path(cwd) if isinstance(cwd, str) and cwd else root
    decision = decide(config, role, root, target, base)
    if decision.allowed:
        return None, ""
    return _deny(
        f"[harness] Role {role!r} may not write {decision.path}: {decision.reason}. "
        f"Write scopes are in {CONFIG_PATH}; ask the lead or the user if this is needed."
    ), ""


def run_pre_tool_use(stdin: TextIO, stdout: TextIO, stderr: TextIO, environ=None) -> int:
    environ = dict(os.environ if environ is None else environ)
    raw = stdin.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        print("harness hook: input too large; not enforcing", file=stderr)
        return 0
    try:
        payload = json.loads(raw)
    except ValueError:
        print("harness hook: input is not JSON; not enforcing", file=stderr)
        return 0
    if not isinstance(payload, dict):
        print("harness hook: input is not a JSON object; not enforcing", file=stderr)
        return 0
    output, note = evaluate(payload, environ)
    if note:
        print(note, file=stderr)
    if output is not None:
        stdout.write(json.dumps(output, ensure_ascii=False) + "\n")
    return 0


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
        temporary = target.with_name(f".{target.name}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return {"changed": True, "dry_run": dry_run, "path": SETTINGS_PATH, "settings": settings}
