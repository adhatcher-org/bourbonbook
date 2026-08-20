"""Where a setting's value comes from, and what a save is allowed to write.

`Settings.from_env` layers the managed `.env` over `os.environ`, so the file wins. That is
correct -- an admin UI that cannot override the deployed environment is useless -- but it
made two silent failures possible.

The first is a dropped key: `read_managed_config` discards anything no `ConfigField` owns,
so an operator can set a key, see it sitting in the file, and never have it take effect.
The second is a frozen key: the old writer persisted every registered field on every save,
so a single unrelated edit captured every environment-inherited value into the file, where
it silently outranked any later container change. Rotating a secret in the container would
then appear to do nothing.

These tests pin both behaviours down.
"""

from __future__ import annotations

from pathlib import Path

from bourbonbook.admin_config import (
    CONFIG_FIELDS,
    MANAGED_HEADER,
    config_sources,
    persisted_keys,
    read_managed_config,
    unregistered_env_entries,
    write_managed_config,
)
from bourbonbook.config import Settings

ALL_KEYS = tuple(field.key for field in CONFIG_FIELDS)


def _values(**overrides: str) -> dict[str, str]:
    """A complete value map, since the writer expects every registered key."""
    values = {key: "" for key in ALL_KEYS}
    values.update(overrides)
    return values


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


# --- unregistered keys are reported, not silently dropped ---------------------


def test_unregistered_key_is_reported(tmp_path: Path) -> None:
    """The failure that motivated this: a key that looks set but does nothing."""
    env = _write(tmp_path / ".env", 'LOG_LEVEL="INFO"\nCATALOG_IMPORT_MAX_SIZE=50M\n')

    assert "CATALOG_IMPORT_MAX_SIZE" in unregistered_env_entries(env)
    assert "LOG_LEVEL" not in unregistered_env_entries(env)


def test_container_runtime_keys_are_not_reported(tmp_path: Path) -> None:
    """TZ is consumed by the container, so flagging it would be a false alarm."""
    env = _write(tmp_path / ".env", 'TZ="America/New_York"\n')

    assert unregistered_env_entries(env) == {}


def test_env_only_settings_are_reported(tmp_path: Path) -> None:
    """Env-only settings really are discarded from this file, so they warrant a warning."""
    env = _write(tmp_path / ".env", "DATA_DIR=/somewhere/else\n")

    assert "DATA_DIR" in unregistered_env_entries(env)


def test_registered_image_budgets_now_take_effect(tmp_path: Path) -> None:
    """These two were silently ignored before being given ConfigFields."""
    env = _write(tmp_path / ".env", 'CATALOG_IMPORT_MAX_IMAGE_DIMENSION="4000"\n')

    assert read_managed_config(env)["CATALOG_IMPORT_MAX_IMAGE_DIMENSION"] == "4000"
    assert unregistered_env_entries(env) == {}


# --- provenance ---------------------------------------------------------------


def test_config_sources_separates_the_three_tiers(tmp_path: Path) -> None:
    env = _write(tmp_path / ".env", 'LOG_LEVEL="DEBUG"\n')

    sources = config_sources(env, environment={"OLLAMA_URL": "http://ollama:11434"})

    assert sources["LOG_LEVEL"] == "file"
    assert sources["OLLAMA_URL"] == "environment"
    assert sources["SMTP_HOST"] == "default"


def test_file_wins_over_environment_in_provenance(tmp_path: Path) -> None:
    """A key in both places is reported as file-sourced, matching from_env's precedence."""
    env = _write(tmp_path / ".env", 'OLLAMA_URL="http://from-file:11434"\n')

    sources = config_sources(env, environment={"OLLAMA_URL": "http://from-env:11434"})

    assert sources["OLLAMA_URL"] == "file"


def test_baseline_settings_ignore_the_managed_file(monkeypatch, tmp_path: Path) -> None:
    _write(tmp_path / ".env", 'LOG_LEVEL="DEBUG"\n')
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LOG_LEVEL", "WARNING")

    assert Settings.from_env().log_level == "DEBUG"
    assert Settings.from_env(include_managed=False).log_level == "WARNING"


# --- what a save persists -----------------------------------------------------


def test_value_matching_the_environment_is_not_persisted() -> None:
    """The regression that motivated the filtered write."""
    keys = persisted_keys(
        submitted={"OLLAMA_URL": "http://ollama:11434"},
        baseline={"OLLAMA_URL": "http://ollama:11434"},
        existing={},
    )

    assert "OLLAMA_URL" not in keys


def test_changed_value_is_persisted() -> None:
    keys = persisted_keys(
        submitted={"OLLAMA_URL": "http://elsewhere:11434"},
        baseline={"OLLAMA_URL": "http://ollama:11434"},
        existing={},
    )

    assert "OLLAMA_URL" in keys


def test_existing_override_survives_an_unrelated_save() -> None:
    keys = persisted_keys(
        submitted={"LOG_LEVEL": "INFO"},
        baseline={"LOG_LEVEL": "INFO"},
        existing={"LOG_LEVEL": "INFO"},
    )

    assert "LOG_LEVEL" in keys


def test_revert_drops_a_key_even_when_it_is_in_the_file() -> None:
    keys = persisted_keys(
        submitted={"LOG_LEVEL": "DEBUG"},
        baseline={"LOG_LEVEL": "INFO"},
        existing={"LOG_LEVEL": "DEBUG"},
        reverted={"LOG_LEVEL"},
    )

    assert "LOG_LEVEL" not in keys


# --- the writer ---------------------------------------------------------------


def test_writer_persists_only_the_named_keys(tmp_path: Path) -> None:
    env = tmp_path / ".env"

    write_managed_config(env, _values(LOG_LEVEL="DEBUG", SMTP_HOST="mail"), persist={"LOG_LEVEL"})

    written = read_managed_config(env)
    assert written == {"LOG_LEVEL": "DEBUG"}
    assert "SMTP_HOST" not in env.read_text(encoding="utf-8")


def test_environment_sourced_secret_is_not_captured_by_an_unrelated_save(tmp_path: Path) -> None:
    """End to end: saving one field must not freeze an inherited secret into the file.

    This is the rotation trap -- once OPENAI_API_KEY lands in the managed file it outranks
    the container, so rotating it there would silently keep using the stale value.
    """
    env = tmp_path / ".env"
    baseline = _values(OPENAI_API_KEY="sk-from-environment", LOG_LEVEL="INFO")
    submitted = dict(baseline, LOG_LEVEL="DEBUG")

    write_managed_config(
        env, submitted, persist=persisted_keys(submitted, baseline, read_managed_config(env))
    )

    assert "OPENAI_API_KEY" not in env.read_text(encoding="utf-8")
    assert read_managed_config(env) == {"LOG_LEVEL": "DEBUG"}


def test_writer_preserves_comments_and_unregistered_lines(tmp_path: Path) -> None:
    env = _write(
        tmp_path / ".env",
        '# operator note worth keeping\nCATALOG_IMPORT_MAX_SIZE=50M\nLOG_LEVEL="INFO"\n',
    )

    write_managed_config(env, _values(LOG_LEVEL="DEBUG"), persist={"LOG_LEVEL"})

    text = env.read_text(encoding="utf-8")
    assert "# operator note worth keeping" in text
    assert "CATALOG_IMPORT_MAX_SIZE=50M" in text


def test_repeated_saves_do_not_accumulate_headers(tmp_path: Path) -> None:
    env = tmp_path / ".env"

    for _ in range(3):
        write_managed_config(env, _values(LOG_LEVEL="DEBUG"), persist={"LOG_LEVEL"})

    assert env.read_text(encoding="utf-8").count(MANAGED_HEADER) == 1


def test_writer_defaults_to_every_key(tmp_path: Path) -> None:
    """Omitting persist keeps the original behaviour for any caller that relies on it."""
    env = tmp_path / ".env"

    write_managed_config(env, _values(LOG_LEVEL="DEBUG"))

    assert set(read_managed_config(env)) == set(ALL_KEYS)
