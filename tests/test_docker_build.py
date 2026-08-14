from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from scripts.docker_build import build_image, dockerfile_base_images, is_transient_registry_error

ROOT = Path(__file__).resolve().parents[1]

BUILDER_IMAGE = "example.invalid/builder:1.0"
RUNTIME_IMAGE = "example.invalid/runtime:2.0"
FIXTURE_DOCKERFILE = f"""\
FROM {BUILDER_IMAGE} AS tools
FROM {RUNTIME_IMAGE} AS runtime
COPY --from=tools /tool /bin/
CMD ["/bin/tool"]
"""


@pytest.fixture
def dockerfile(tmp_path: Path) -> Path:
    """A two-stage Dockerfile whose base images never change, unlike the real one."""
    path = tmp_path / "Dockerfile"
    path.write_text(FIXTURE_DOCKERFILE, encoding="utf-8")
    return path


def repository(image: str) -> str:
    """Strip the tag from an image reference so assertions survive version bumps."""
    name, separator, tag = image.rpartition(":")
    return name if separator and "/" not in tag else image


class FakeRunner:
    def __init__(self, results: list[subprocess.CompletedProcess[str]]) -> None:
        self.results = iter(results)
        self.commands: list[Sequence[str]] = []

    def __call__(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        return next(self.results)


def result(returncode: int, output: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=(), returncode=returncode, stdout=output)


def test_dockerfile_base_images_excludes_named_stages_and_platform_flags(tmp_path: Path) -> None:
    path = tmp_path / "Dockerfile"
    path.write_text(
        "FROM --platform=$BUILDPLATFORM example.invalid/builder:1.0 AS tools\n"
        "FROM example.invalid/runtime:2.0 AS runtime\n"
        "FROM tools AS extra\n",
        encoding="utf-8",
    )

    assert dockerfile_base_images(path) == [
        "example.invalid/builder:1.0",
        "example.invalid/runtime:2.0",
    ]


def test_real_dockerfile_declares_the_expected_external_repositories() -> None:
    images = dockerfile_base_images(ROOT / "Dockerfile")

    assert [repository(image) for image in images] == ["docker.io/astral/uv", "python"]
    assert all(":" in image.rpartition("/")[2] for image in images), (
        f"every base image must be pinned to a tag: {images}"
    )


@pytest.mark.parametrize(
    "output",
    [
        "received unexpected HTTP status: 429 Too Many Requests",
        "received unexpected HTTP status: 503 Service Unavailable",
        "network timeout while contacting registry",
        "connection reset by peer",
        "temporary failure in name resolution",
    ],
)
def test_transient_registry_error_categories_are_retryable(output: str) -> None:
    assert is_transient_registry_error(output)


@pytest.mark.parametrize(
    "output",
    [
        "pull access denied for private/image",
        "manifest unknown: manifest not found",
        "x509: certificate signed by unknown authority after timeout",
        "Cannot connect to the Docker daemon",
        "no space left on device",
        "parse error in image reference",
        "an unrecognized pull failure",
    ],
)
def test_terminal_or_unrecognized_pull_errors_are_not_retryable(output: str) -> None:
    assert not is_transient_registry_error(output)


def test_transient_pull_retries_with_backoff_then_builds_once(dockerfile: Path) -> None:
    runner = FakeRunner(
        [
            result(1, "received unexpected HTTP status: 429 Too Many Requests"),
            result(1, "received unexpected HTTP status: 502 Bad Gateway"),
            result(0),
            result(0),
            result(0),
        ]
    )
    delays: list[float] = []

    outcome = build_image(
        dockerfile=dockerfile,
        image="test-image",
        tag="test",
        runner=runner,
        sleeper=delays.append,
    )

    assert outcome.returncode == 0
    assert delays == [30, 60]
    assert runner.commands == [
        ("docker", "pull", BUILDER_IMAGE),
        ("docker", "pull", BUILDER_IMAGE),
        ("docker", "pull", BUILDER_IMAGE),
        ("docker", "pull", RUNTIME_IMAGE),
        ("docker", "build", "--pull=false", "--tag", "test-image:test", "."),
    ]


def test_transient_pull_stops_after_four_attempts_without_final_sleep(dockerfile: Path) -> None:
    runner = FakeRunner([result(1, "network timeout") for _ in range(4)])
    delays: list[float] = []

    outcome = build_image(
        dockerfile=dockerfile,
        image="test-image",
        tag="test",
        runner=runner,
        sleeper=delays.append,
    )

    assert outcome.returncode == 1
    assert delays == [30, 60, 120]
    assert [command[:2] for command in runner.commands] == [("docker", "pull")] * 4


def test_non_transient_pull_failure_does_not_retry_or_sleep(dockerfile: Path) -> None:
    runner = FakeRunner([result(1, "pull access denied for private/image")])
    delays: list[float] = []

    outcome = build_image(
        dockerfile=dockerfile,
        image="test-image",
        tag="test",
        runner=runner,
        sleeper=delays.append,
    )

    assert outcome.returncode == 1
    assert delays == []
    assert runner.commands == [("docker", "pull", BUILDER_IMAGE)]


def test_build_failure_is_returned_once_without_retry_even_with_timeout_text(
    dockerfile: Path,
) -> None:
    runner = FakeRunner(
        [
            result(0),
            result(0),
            result(1, "RUN dependency install failed after network timeout"),
        ]
    )
    delays: list[float] = []

    outcome = build_image(
        dockerfile=dockerfile,
        image="test-image",
        tag="test",
        runner=runner,
        sleeper=delays.append,
    )

    assert outcome.returncode == 1
    assert delays == []
    assert runner.commands[-1] == (
        "docker",
        "build",
        "--pull=false",
        "--tag",
        "test-image:test",
        ".",
    )
    assert len(runner.commands) == 3
