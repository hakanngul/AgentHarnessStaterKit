"""Readable one-line descriptions of engine events, shared by the CLI and the server."""

from agent_harness.engine import events as events_


def describe_event(event) -> str | None:
    """One readable line for an engine event, or None when it is not worth showing."""
    if isinstance(event, events_.Started):
        return f"session {event.session_id}"
    if isinstance(event, events_.Text):
        return event.text.strip()
    if isinstance(event, events_.ToolUse):
        detail = event.input.get("command") or event.input.get("file_path") or ""
        return f"[{event.name}] {detail}".rstrip()
    if isinstance(event, events_.ToolDenied):
        return f"[denied {event.name}] {event.reason}"
    if isinstance(event, events_.Waiting):
        return f"[usage limit] waiting {int(event.seconds)}s, then resuming the session"
    if isinstance(event, events_.RateLimit) and event.status != "allowed":
        return f"[usage limit {event.status}] resets at {event.resets_at}"
    return None
