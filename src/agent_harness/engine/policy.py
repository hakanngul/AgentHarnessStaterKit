"""Which tool calls an unattended agent may make, decided from the role config.

Default is deny. Allowed: file-writing tools inside the role's write scope (the rule
`harness scope` and the PreToolUse hook apply), a short list of read-only tools whose
paths stay inside the project, and shell commands that start with one of the role's
`bash` prefixes. Shell commands must not chain, pipe, redirect or substitute, because
any of those could run a second, unchecked command. `harness evidence run` is allowed
only when the command after `--` is itself in the role's `bash` list. Network tools,
MCP tools and tools this module does not know are denied.

This is a guardrail for the agent's own requests, not a sandbox: an allowed command
such as a test runner can still write files, and reads follow symlinks inside the project.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from agent_harness.models import HarnessConfig
from agent_harness.roles import decide

WRITE_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path"}
NOTEBOOK_TOOL = "NotebookEdit"
SHELL_TOOL = "Bash"
# Read-only tools and the input fields that carry a path they may read.
READ_TOOLS = {
    "Read": ("file_path",),
    "NotebookRead": ("notebook_path",),
    "Glob": ("path",),
    "Grep": ("path",),
    "LS": ("path",),
    "BashOutput": (),
    "TodoWrite": (),
    # Subagents and skills run under the same policy: their tool calls reach it too.
    "Task": (),
    "Agent": (),
    "Skill": (),
}
DENIED_KINDS = (
    ("mcp__", "MCP tools are not allowed for unattended runs"),
    ("Web", "network tools are not allowed for unattended runs"),
)
EVIDENCE_COMMANDS = (("harness", "evidence", "run"), ("uv", "run", "harness", "evidence", "run"))
# Characters that would let one command line run, feed or redirect another command.
SHELL_META = (";", "&", "|", "<", ">", "`", "$(", "\n", "\r")


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    reason: str = ""


def _matches(words: list[str], prefixes: list[str]) -> bool:
    return any(words[: len(prefix.split())] == prefix.split() for prefix in prefixes)


def _not_listed(prefixes: list[str]) -> Verdict:
    allowed = ", ".join(prefixes) if prefixes else "none"
    return Verdict(False, f"command is not in this role's bash list (allowed: {allowed})")


def _evidence(words: list[str], prefixes: list[str]) -> Verdict:
    if "--" not in words:
        return Verdict(False, "harness evidence run needs `-- <command>`")
    inner = words[words.index("--") + 1 :]
    if not inner:
        return Verdict(False, "harness evidence run needs a command after `--`")
    if not _matches(inner, prefixes):
        return Verdict(False, "the command after `--` is not in this role's bash list")
    return Verdict(True)


def _shell(command: str, prefixes: list[str]) -> Verdict:
    command = command.strip()
    if not command:
        return Verdict(False, "empty command")
    if any(mark in command for mark in SHELL_META):
        return Verdict(
            False,
            "chained, piped, redirected or substituted commands are not allowed; "
            "run one command at a time",
        )
    words = command.split()
    if any(tuple(words[: len(head)]) == head for head in EVIDENCE_COMMANDS):
        return _evidence(words, prefixes)
    return Verdict(True) if _matches(words, prefixes) else _not_listed(prefixes)


def _inside(root: Path, target: str) -> bool:
    candidate = Path(os.path.abspath(root / os.path.expanduser(target)))
    return candidate == root or root in candidate.parents


def check_tool(
    config: HarnessConfig, role: str, root: Path, tool: str, tool_input: dict
) -> Verdict:
    """Allow or deny one tool call made by an agent acting as `role`."""
    role_config = config.roles.get(role)
    if role_config is None:
        return Verdict(False, f"role {role!r} is not defined in .harness/config.yaml")
    for prefix, reason in DENIED_KINDS:
        if tool.startswith(prefix):
            return Verdict(False, reason)
    if tool == SHELL_TOOL:
        command = tool_input.get("command")
        if not isinstance(command, str):
            return Verdict(False, "Bash call without a command")
        return _shell(command, role_config.bash)
    if tool in READ_TOOLS:
        for key in READ_TOOLS[tool]:
            target = tool_input.get(key)
            if isinstance(target, str) and target and not _inside(root, target):
                return Verdict(False, f"{tool} may only read inside the project")
        return Verdict(True)
    key = WRITE_TOOLS.get(tool) or ("notebook_path" if tool == NOTEBOOK_TOOL else None)
    if key is None:
        return Verdict(False, f"tool {tool!r} is not on the unattended allow list")
    target = tool_input.get(key)
    if not isinstance(target, str) or not target:
        return Verdict(False, f"{tool} call without a {key}")
    # Relative paths are relative to the agent's working directory, the project root.
    decision = decide(config, role, root, target, cwd=root)
    return Verdict(decision.allowed, "" if decision.allowed else decision.reason)
