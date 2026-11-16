"""Claude engine: runs a request through the Claude Agent SDK on the local login.

The SDK is an optional dependency (`pip install agent-harness-starter[engine]`). It is
imported only when a run starts, so the rest of the CLI works without it. The session
loads the project's own Claude settings (`setting_sources=["project"]`), and every
permission prompt is answered by the role policy instead of a person.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from agent_harness.engine.events import (
    Event,
    Finished,
    RateLimit,
    RunRequest,
    Started,
    Text,
    ToolDenied,
    ToolUse,
)
from agent_harness.engine.policy import Verdict, check_tool
from agent_harness.filesystem import HarnessError
from agent_harness.roles import load_config

INSTALL_HINT = "the Claude engine needs the SDK: pip install 'agent-harness-starter[engine]'"


def load_sdk() -> Any:
    try:
        import claude_agent_sdk
    except ImportError as exc:
        raise HarnessError(INSTALL_HINT) from exc
    return claude_agent_sdk


def translate(message: Any, sdk: Any) -> list[Event]:
    """Engine events for one SDK message; unknown message kinds yield nothing."""
    if isinstance(message, sdk.SystemMessage):
        session = message.data.get("session_id") if message.subtype == "init" else None
        return [Started(session)] if isinstance(session, str) and session else []
    if isinstance(message, sdk.AssistantMessage):
        events: list[Event] = []
        for block in message.content:
            if isinstance(block, sdk.TextBlock) and block.text.strip():
                events.append(Text(block.text))
            elif isinstance(block, sdk.ToolUseBlock):
                events.append(ToolUse(block.name, dict(block.input)))
        return events
    if isinstance(message, sdk.RateLimitEvent):
        info = message.rate_limit_info
        return [RateLimit(info.status, info.resets_at)]
    if isinstance(message, sdk.ResultMessage):
        errors = "; ".join(message.errors or [])
        return [
            Finished(
                ok=not message.is_error,
                session_id=message.session_id,
                result=message.result or "",
                error=errors or ((message.result or "") if message.is_error else ""),
                turns=message.num_turns,
                cost_usd=message.total_cost_usd,
                http_status=message.api_error_status,
            )
        ]
    return []


def build_options(request: RunRequest, sdk: Any, can_use_tool: Any, hooks: Any = None) -> Any:
    return sdk.ClaudeAgentOptions(
        cwd=str(request.cwd),
        model=request.model,
        effort=request.effort,
        resume=request.resume,
        max_turns=request.max_turns,
        permission_mode="default",
        can_use_tool=can_use_tool,
        setting_sources=["project"],
        hooks=hooks,
    )


class ClaudeEngine:
    def __init__(self, sdk: Any = None) -> None:
        self._sdk = sdk

    async def run(self, request: RunRequest, root: Path) -> AsyncIterator[Event]:
        sdk = self._sdk or load_sdk()
        config = load_config(root)
        denied: list[Event] = []

        def verdict_for(tool: str, tool_input: dict) -> Any:
            verdict = check_tool(config, request.role, root, tool, tool_input)
            if not verdict.allowed:
                denied.append(ToolDenied(tool, verdict.reason))
            return verdict

        async def pre_tool_use(input_data: dict, tool_use_id: Any, context: Any) -> dict:
            # Runs before permission rules, so allow rules in the project's own settings
            # cannot shadow the policy the way they can shadow `can_use_tool`.
            try:
                tool, tool_input = input_data["tool_name"], input_data["tool_input"]
                if not isinstance(tool, str) or not isinstance(tool_input, dict):
                    raise TypeError("malformed PreToolUse input")
                verdict = verdict_for(tool, tool_input)
            except Exception as exc:  # a hook error would let the call through: deny instead
                name = input_data.get("tool_name") if isinstance(input_data, dict) else None
                verdict = Verdict(False, f"policy error, call denied: {type(exc).__name__}: {exc}")
                denied.append(ToolDenied(str(name), verdict.reason))
            if verdict.allowed:
                return {}
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": verdict.reason,
                }
            }

        async def can_use_tool(tool: str, tool_input: dict, context: Any) -> Any:
            verdict = verdict_for(tool, tool_input)
            if verdict.allowed:
                return sdk.PermissionResultAllow()
            return sdk.PermissionResultDeny(message=verdict.reason)

        hooks = {"PreToolUse": [sdk.HookMatcher(hooks=[pre_tool_use])]}
        options = build_options(request, sdk, can_use_tool, hooks)
        async with sdk.ClaudeSDKClient(options=options) as client:
            await client.query(request.prompt)
            async for message in client.receive_response():
                while denied:
                    yield denied.pop(0)
                for event in translate(message, sdk):
                    yield event
        while denied:
            yield denied.pop(0)
