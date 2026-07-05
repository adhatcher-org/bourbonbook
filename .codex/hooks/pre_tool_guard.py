"""Conservative repository guard for Codex PreToolUse shell hooks."""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import PurePath

READ_COMMANDS = {"awk", "cat", "grep", "head", "less", "more", "rg", "sed", "tail"}
SENSITIVE_NAMES = {".coverage", "coverage.xml"}
SENSITIVE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


def _tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _is_secret_env(token: str) -> bool:
    name = PurePath(token.strip(";&|()")).name
    return name != ".env.example" and (name == ".env" or name.startswith(".env."))


def _is_sensitive_artifact(token: str) -> bool:
    path = PurePath(token.strip(";&|()"))
    return (
        path.name in SENSITIVE_NAMES
        or path.suffix.lower() in SENSITIVE_SUFFIXES
        or "uploads" in path.parts
    )


def _deny(reason: str) -> dict[str, object]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def evaluate(command: str) -> dict[str, object] | None:
    tokens = _tokens(command)
    lowered = [token.lower() for token in tokens]

    if re.search(r"\bgit\s+reset\s+--hard\b", command):
        return _deny("Destructive `git reset --hard` is blocked by Bourbon Book repository policy.")
    if re.search(r"\bgit\s+clean\b[^;&|]*(?:--force\b|-[a-zA-Z]*f)", command):
        return _deny("Forced `git clean` is blocked because it can destroy untracked user work.")
    if re.search(r"\bgit\s+add\b[^;&|]*(?:\s)(?:\.|-A|--all)(?=\s|$)", command):
        return _deny("Broad staging is blocked; stage only explicit files belonging to the task.")

    is_git_add = "git" in lowered and "add" in lowered
    if is_git_add and any(_is_secret_env(token) for token in tokens):
        return _deny("Staging secret environment files is blocked. `.env.example` remains allowed.")
    if is_git_add and any(_is_sensitive_artifact(token) for token in tokens):
        return _deny("Staging databases, uploads, or generated coverage artifacts is blocked.")

    reads_files = any(PurePath(token).name in READ_COMMANDS for token in tokens)
    if reads_files and any(_is_secret_env(token) for token in tokens):
        return _deny(
            "Directly reading secret environment files is blocked; inspect `.env.example`."
        )

    live_markers = ("api.openai.com", "ollama.aaronhatcher.com", "qdrant.aaronhatcher.com")
    test_command = re.search(r"\b(pytest|make\s+(?:test|coverage|pre-ci|ci|pr-review))\b", command)
    if test_command and any(marker in command for marker in live_markers):
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": (
                    "This test command appears to reference a live provider. Bourbon Book's "
                    "deterministic tests must use fakes and fixtures unless the user explicitly "
                    "authorized a live evaluation."
                ),
            }
        }
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    command = payload.get("tool_input", {}).get("command", "")
    if not isinstance(command, str):
        return 0
    result = evaluate(command)
    if result is not None:
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
