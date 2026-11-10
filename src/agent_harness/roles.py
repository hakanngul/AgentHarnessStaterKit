"""Role write scopes from `.harness/config.yaml` and the allow/deny decision.

Glob syntax (project-relative POSIX paths):
  `*` any characters except `/`, `?` one character except `/`,
  `**` any number of path segments (including none), a trailing `/` means `/**`.
`deny` is applied before `write`; a path that matches neither is denied.
Matching ignores case and Unicode normalization form, because common filesystems
(macOS, Windows) do: `.CLAUDE/x` must not slip past a `.claude/**` deny rule.
"""

import os
import re
import unicodedata
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml
from pydantic import ValidationError

from agent_harness.filesystem import HarnessError, load_yaml, managed_path, read_text
from agent_harness.models import HarnessConfig

CONFIG_PATH = ".harness/config.yaml"


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


@cache
def _compile(pattern: str) -> re.Pattern[str]:
    pattern = _fold(pattern)
    if pattern.endswith("/"):
        pattern += "**"
    parts: list[str] = []
    segments = pattern.split("/")
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        if segment == "**":
            parts.append(".*" if last else "(?:[^/]+/)*")
            continue
        body = "".join(
            "[^/]*" if char == "*" else "[^/]" if char == "?" else re.escape(char)
            for char in segment
        )
        parts.append(body if last else body + "/")
    return re.compile("".join(parts))


def glob_match(pattern: str, path: str) -> bool:
    return _compile(pattern).fullmatch(_fold(path)) is not None


def load_config(root: Path) -> HarnessConfig:
    try:
        raw = load_yaml(read_text(managed_path(root, CONFIG_PATH)))
        return HarnessConfig.model_validate(raw)
    except ValidationError as exc:
        raise HarnessError(f"Invalid configuration: {exc.errors(include_input=False)}") from exc
    except (ValueError, RecursionError, yaml.YAMLError) as exc:
        raise HarnessError(f"Invalid configuration: {exc}") from exc


@dataclass(frozen=True)
class Decision:
    path: str
    allowed: bool
    reason: str


def relative_target(
    root: Path, target: str, cwd: Path | None = None, aliases: tuple[Path, ...] = ()
) -> str:
    """Project-relative POSIX path for a target, rejecting escapes and symlinks.

    `..` is refused outright: text normalization would collapse it before the symlink
    check, while the kernel resolves it after following a symlink. `aliases` are other
    spellings of root, e.g. the unresolved project path when root is its realpath.
    """
    if ".." in Path(target).parts:
        raise HarnessError("'..' segments are not allowed in write targets")
    base = cwd if cwd is not None else root
    candidate = Path(os.path.abspath(base / os.path.expanduser(target)))
    for anchor in (root, *aliases):
        try:
            relative = candidate.relative_to(anchor).as_posix()
            break
        except ValueError:
            continue
    else:
        raise HarnessError(f"outside the project: {candidate}")
    if relative == ".":
        raise HarnessError("the project root itself is not a writable file")
    managed_path(root, relative)  # rejects symlinked components
    return relative


def decide(
    config: HarnessConfig,
    role: str,
    root: Path,
    target: str,
    cwd: Path | None = None,
    aliases: tuple[Path, ...] = (),
) -> Decision:
    role_config = config.roles.get(role)
    if role_config is None:
        return Decision(target, False, f"unknown role {role!r}")
    try:
        relative = relative_target(root, target, cwd, aliases)
    except (HarnessError, OSError, ValueError) as exc:
        return Decision(target, False, str(exc))
    for pattern in role_config.deny:
        if glob_match(pattern, relative):
            return Decision(relative, False, f"denied by {pattern!r}")
    for pattern in role_config.write:
        if glob_match(pattern, relative):
            return Decision(relative, True, f"allowed by {pattern!r}")
    return Decision(relative, False, "not in the role's write scope")
