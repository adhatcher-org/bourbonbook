from pathlib import Path

from scripts.pr_review import check_tracked_files


def test_review_check_rejects_sensitive_files_and_conflict_markers(tmp_path: Path) -> None:
    environment = tmp_path / ".env"
    environment.write_text("SECRET=do-not-commit\n")
    conflicted = tmp_path / "module.py"
    conflicted.write_text("<<<<<<< ours\nvalue = 1\n=======\nvalue = 2\n>>>>>>> theirs\n")

    failures = check_tracked_files([environment, conflicted])

    assert any("sensitive generated file" in failure for failure in failures)
    assert any("merge-conflict marker" in failure for failure in failures)


def test_review_check_accepts_normal_source_and_ignores_binary(tmp_path: Path) -> None:
    source = tmp_path / "module.py"
    source.write_text("value = 'safe'\n")
    binary = tmp_path / "image.bin"
    binary.write_bytes(b"<<<<<<< not-source")

    assert check_tracked_files([source, binary]) == []


def test_make_help_exposes_required_contract() -> None:
    makefile = Path(__file__).parents[1] / "Makefile"
    content = makefile.read_text()
    required = {
        "install",
        "build",
        "build-local",
        "pre-ci",
        "ci",
        "test",
        "coverage",
        "run_local",
        "update",
        "lint",
        "pr-review",
        "security",
        "dependency-check",
    }

    assert all(f"{target}:" in content for target in required)
