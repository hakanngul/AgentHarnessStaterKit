"""Which tool calls an unattended agent may make, decided from the role config.

File-writing tools must stay inside the role's write scope (the same rule the
PreToolUse hook applies). Shell commands must start with one of the role's `bash`
prefixes and must not chain, pipe, redirect or substitute, because any of those could
run a second, unchecked command. MCP tools are denied, because the project's settings
can add servers whose tools write files and this policy cannot tell which. Other
built-in tools, which only read or search, are allowed.

This is a guardrail for the agent's own requests, not a sandbox: an allowed command
such as a test runner can still write files.
"""

from dataclasses import dataclass
from pathlib import Path

from agent_harness.models import HarnessConfig
from agent_harness.roles import decide

WRITE_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path"}
NOTEBOOK_TOOL = "NotebookEdit"
SHELL_TOOL = "Bash"
MCP_PREFIX = "mcp__"
# Characters that would let one command line run, feed or redirect another command.
SHELL_META = (";", "&", "|", "<", ">", "`", "$(", "\n", "\r")


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    reason: str = ""


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
    for prefix in prefixes:
        expected = prefix.split()
        if words[: len(expected)] == expected:
            return Verdict(True)
    allowed = ", ".join(prefixes) if prefixes else "none"
    return Verdict(False, f"command is not in this role's bash list (allowed: {allowed})")


def check_tool(
    config: HarnessConfig, role: str, root: Path, tool: str, tool_input: dict
) -> Verdict:
    """Allow or deny one tool call made by an agent acting as `role`."""
    role_config = config.roles.get(role)
    if role_config is None:
        return Verdict(False, f"role {role!r} is not defined in .harness/config.yaml")
    if tool.startswith(MCP_PREFIX):
        return Verdict(False, "MCP tools are not allowed for unattended runs")
    if tool == SHELL_TOOL:
        command = tool_input.get("command")
        if not isinstance(command, str):
            return Verdict(False, "Bash call without a command")
        return _shell(command, role_config.bash)
    key = WRITE_TOOLS.get(tool) or ("notebook_path" if tool == NOTEBOOK_TOOL else None)
    if key is None:
        return Verdict(True)
    target = tool_input.get(key)
    if not isinstance(target, str) or not target:
        return Verdict(False, f"{tool} call without a {key}")
    # Relative paths are relative to the agent's working directory, the project root.
    decision = decide(config, role, root, target, cwd=root)
    return Verdict(decision.allowed, "" if decision.allowed else decision.reason)
