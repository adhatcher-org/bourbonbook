"""Retry narrowly scoped Docker registry preflights before one image build."""

from __future__ import annotations

import argparse
import re
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKOFF_SECONDS = (30, 60, 120)
MAX_PULL_ATTEMPTS = len(BACKOFF_SECONDS) + 1
CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
Sleeper = Callable[[float], None]
FROM_PATTERN = re.compile(r"^\s*FROM\s+(?:--platform=\S+\s+)?(?P<image>\S+)", re.IGNORECASE)
TRANSIENT_REGISTRY_ERROR = re.compile(
    r"(?:\b429\b|\b5\d{2}\b|timed?\s*out|connection\s+(?:was\s+)?reset|"
    r"reset\s+by\s+peer|temporary(?:\s+failure)?\s+(?:in\s+)?(?:dns|name\s+resolution|resolv))",
    re.IGNORECASE,
)
NON_RETRYABLE_PULL_ERROR = re.compile(
    r"(?:\btls\b|certificate|auth(?:entication|orization)?|unauthorized|denied|"
    r"manifest (?:unknown|not found)|\bdaemon\b|no space left|\bdisk\b|parse error)",
    re.IGNORECASE,
)


def dockerfile_base_images(dockerfile: Path) -> list[str]:
    """Return external base images in Dockerfile order, excluding named build stages."""
    images: list[str] = []
    stage_names: set[str] = set()
    for line in dockerfile.read_text(encoding="utf-8").splitlines():
        match = FROM_PATTERN.match(line)
        if not match:
            continue
        image = match.group("image")
        if image.lower() not in stage_names:
            images.append(image)
        alias = re.search(r"\s+AS\s+(\S+)\s*$", line, re.IGNORECASE)
        if alias:
            stage_names.add(alias.group(1).lower())
    return images


def is_transient_registry_error(output: str) -> bool:
    """Recognize only retry-safe registry and transport failures.

    Terminal pull failures take precedence when their output also happens to
    mention a transient-looking transport term (for example, TLS timeout).
    """
    return not NON_RETRYABLE_PULL_ERROR.search(output) and bool(
        TRANSIENT_REGISTRY_ERROR.search(output)
    )


def default_runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def show_output(result: subprocess.CompletedProcess[str]) -> None:
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")


def preflight_image(
    image: str, runner: CommandRunner, sleeper: Sleeper
) -> subprocess.CompletedProcess[str]:
    """Pull one base image, retrying only classified transient output."""
    for attempt in range(MAX_PULL_ATTEMPTS):
        result = runner(("docker", "pull", image))
        show_output(result)
        if result.returncode == 0:
            return result
        if not is_transient_registry_error(result.stdout or ""):
            return result
        if attempt < len(BACKOFF_SECONDS):
            sleeper(BACKOFF_SECONDS[attempt])
    return result


def build_image(
    *,
    dockerfile: Path,
    image: str,
    tag: str,
    runner: CommandRunner = default_runner,
    sleeper: Sleeper = time.sleep,
) -> subprocess.CompletedProcess[str]:
    """Pull every external base once successfully, then execute one non-pulling build."""
    for base_image in dockerfile_base_images(dockerfile):
        result = preflight_image(base_image, runner, sleeper)
        if result.returncode:
            return result
    result = runner(("docker", "build", "--pull=false", "--tag", f"{image}:{tag}", "."))
    show_output(result)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dockerfile", type=Path, default=ROOT / "Dockerfile")
    parser.add_argument("--image", required=True)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args(argv)
    return build_image(dockerfile=args.dockerfile, image=args.image, tag=args.tag).returncode


if __name__ == "__main__":
    raise SystemExit(main())
