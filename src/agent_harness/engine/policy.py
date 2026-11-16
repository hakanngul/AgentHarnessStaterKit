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
import re
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
# Fields holding a glob that must not point outside the project.
READ_PATTERNS = {"Glob": ("pattern",), "Grep": ("glob",)}
DENIED_KINDS = (
    ("mcp__", "MCP tools are not allowed for unattended runs"),
    ("Web", "network tools are not allowed for unattended runs"),
)
EVIDENCE_OPTIONS = {"--cwd", "--id", "--timeout", "--summary"}  # each takes one value
EVIDENCE_FLAGS = {"--json"}
TASK_ID = re.compile(r"^[A-Z][A-Z0-9]*-[1-9][0-9]*$")
EVIDENCE_COMMANDS = (("harness", "evidence", "run"), ("uv", "run", "harness", "evidence", "run"))
# Characters that would let one command line run, feed or redirect another command.
# The Bash tool runs the command through a shell, but the policy splits it on whitespace.
# Anything a shell would treat specially (quotes, escapes, expansion, globs) makes the two
# views differ, so it is refused.
SHELL_META = (
    ";",
    "&",
    "|",
    "<",
    ">",
    "`",
    "$",
    "\n",
    "\r",
    '"',
    "'",
    "\\",
    "*",
    "?",
    "{",
    "}",
    "~",
    "#",
)


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    reason: str = ""


def _matches(words: list[str], prefixes: list[str]) -> bool:
    return any(words[: len(prefix.split())] == prefix.split() for prefix in prefixes)


def _not_listed(prefixes: list[str]) -> Verdict:
    allowed = ", ".join(prefixes) if prefixes else "none"
    return Verdict(False, f"command is not in this role's bash list (allowed: {allowed})")


def _evidence(words: list[str], prefixes: list[str], root: Path) -> Verdict:
    """`harness evidence run TASK [options] -- COMMAND`, nothing else.

    The command line parser also collects words before `--` into the command, so the
    only safe shape is the task id, known options with one value each, then `--`.
    """
    rest = words[
        next(len(head) for head in EVIDENCE_COMMANDS if tuple(words[: len(head)]) == head) :
    ]
    if not rest or not TASK_ID.match(rest[0]):
        return Verdict(False, "harness evidence run needs a task id such as T-1")
    index = 1
    while index < len(rest) and rest[index] != "--":
        option = rest[index]
        if option in EVIDENCE_FLAGS:
            index += 1
            continue
        if option not in EVIDENCE_OPTIONS:
            return Verdict(False, f"harness evidence run: {option!r} is not allowed here")
        if index + 1 >= len(rest) or rest[index + 1] == "--" or rest[index + 1].startswith("-"):
            return Verdict(False, f"harness evidence run: {option} needs a value")
        if option == "--cwd" and not _inside(root, rest[index + 1]):
            return Verdict(False, "harness evidence run: --cwd must stay inside the project")
        index += 2
    inner = rest[index + 1 :]
    if index >= len(rest) or not inner:
        return Verdict(False, "harness evidence run needs `-- <command>`")
    if not _matches(inner, prefixes):
        return Verdict(False, "the command after `--` is not in this role's bash list")
    return Verdict(True)


def _shell(command: str, prefixes: list[str], root: Path) -> Verdict:
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
        return _evidence(words, prefixes, root)
    return Verdict(True) if _matches(words, prefixes) else _not_listed(prefixes)


def _inside(root: Path, target: str) -> bool:
    candidate = Path(os.path.abspath(root / os.path.expanduser(target)))
    return candidate == root or root in candidate.parents


def _escapes(pattern: str) -> bool:
    # Brace alternatives ({/etc,a}) are checked one by one, as a backend may expand them.
    for part in re.split(r"[{},]", pattern):
        if part.startswith(("/", "~")) or ".." in part.replace("\\", "/").split("/"):
            return True
    return False


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
        return _shell(command, role_config.bash, root)
    if tool in READ_TOOLS:
        for key in READ_TOOLS[tool]:
            target = tool_input.get(key)
            if isinstance(target, str) and target and not _inside(root, target):
                return Verdict(False, f"{tool} may only read inside the project")
        for key in READ_PATTERNS.get(tool, ()):
            pattern = tool_input.get(key)
            if isinstance(pattern, str) and _escapes(pattern):
                return Verdict(False, f"{tool} pattern may only match inside the project")
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
