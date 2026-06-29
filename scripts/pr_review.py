"""Portable repository-integrity checks used before opening a pull request."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {
    ".cfg",
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".yaml",
    ".yml",
}
SENSITIVE_NAMES = {".coverage", ".env", "coverage.xml"}
SENSITIVE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
CONFLICT_MARKERS = ("<<<<<<< ", "=======", ">>>>>>> ")


def run(*command: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
    )


def tracked_files() -> list[Path]:
    result = run("git", "ls-files", "-z", capture=True)
    if result.returncode:
        raise RuntimeError("could not list tracked files")
    return [ROOT / name for name in result.stdout.split("\0") if name]


def check_tracked_files(paths: list[Path]) -> list[str]:
    failures: list[str] = []
    for path in paths:
        relative = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        if path.name in SENSITIVE_NAMES or path.suffix.lower() in SENSITIVE_SUFFIXES:
            failures.append(f"sensitive generated file is tracked: {relative}")
            continue
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if any(line.startswith(CONFLICT_MARKERS) for line in content.splitlines()):
            failures.append(f"unresolved merge-conflict marker in {relative}")
    return failures


def migration_heads() -> list[str]:
    result = run("uv", "run", "alembic", "heads", capture=True)
    if result.returncode:
        raise RuntimeError("Alembic could not inspect migration heads")
    return [line for line in result.stdout.splitlines() if line.strip()]


def main() -> int:
    failures: list[str] = []
    if run("git", "diff", "--check").returncode:
        failures.append("git diff --check found whitespace errors")

    try:
        failures.extend(check_tracked_files(tracked_files()))
        heads = migration_heads()
        if len(heads) != 1:
            failures.append(f"expected one Alembic migration head, found {len(heads)}")
    except RuntimeError as error:
        failures.append(str(error))

    if run("uv", "run", "pytest", "-q", "tests/test_migrations.py").returncode:
        failures.append("migration tests failed")
    if run("docker", "compose", "config", "--quiet").returncode:
        failures.append("Docker Compose configuration is invalid")

    if failures:
        for failure in failures:
            print(f"PR review failed: {failure}", file=sys.stderr)
        return 1
    print("PR diff, tracked files, migration heads, and Compose configuration are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
