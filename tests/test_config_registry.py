"""The configuration registry contract.

`Settings`, `admin_config.CONFIG_FIELDS` and `.env.example` describe the same configuration surface
from three angles, and nothing at runtime forces them to agree. A setting added to `Settings` with
no `ConfigField` keeps working -- it just becomes invisible to the admin UI. A registered field
missing from `.env.example` is admin-editable but undocumented. Both failures are silent, so these
tests are the only thing standing between the codebase and the next accidental omission.

Adding a setting therefore means one of: give it a `ConfigField`, or add it to
`admin_config.ENV_ONLY_SETTINGS` with a reason. There is no third option that passes.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from bourbonbook.admin_config import CONFIG_FIELDS, ENV_ONLY_SETTINGS
from bourbonbook.config import Settings

ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"

# Keys documented in .env.example that deliberately have no ConfigField. DATA_DIR locates the
# managed .env file itself, so the registry must not be able to move it.
UNMANAGED_ENV_EXAMPLE_KEYS = frozenset({"DATA_DIR"})


def _settings_attributes() -> set[str]:
    return {field.name for field in dataclasses.fields(Settings)}


def _registered_attributes() -> set[str]:
    return {field.attribute for field in CONFIG_FIELDS}


def _env_example_keys() -> set[str]:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    return {match.group(1) for match in re.finditer(r"(?m)^([A-Z0-9_]+)=", text)}


def test_every_setting_is_registered_or_explicitly_env_only() -> None:
    """The guard that prevents the next silent omission."""
    unaccounted = _settings_attributes() - _registered_attributes() - set(ENV_ONLY_SETTINGS)

    assert not unaccounted, (
        "Settings attributes with no ConfigField and no ENV_ONLY_SETTINGS entry: "
        f"{sorted(unaccounted)}. Either register the setting in admin_config.CONFIG_FIELDS so it "
        "is admin-editable, or add it to admin_config.ENV_ONLY_SETTINGS with the reason it is "
        "environment-only. Silently omitting it makes the setting invisible rather than broken."
    )


def test_env_only_allowlist_has_no_stale_entries() -> None:
    """An allowlist that outlives its settings stops documenting anything."""
    attributes = _settings_attributes()
    removed = set(ENV_ONLY_SETTINGS) - attributes
    assert not removed, (
        f"ENV_ONLY_SETTINGS names attributes that no longer exist on Settings: {sorted(removed)}"
    )

    also_registered = set(ENV_ONLY_SETTINGS) & _registered_attributes()
    assert not also_registered, (
        "These attributes are both registered in CONFIG_FIELDS and listed as environment-only: "
        f"{sorted(also_registered)}. Registering a setting means deleting its ENV_ONLY_SETTINGS "
        "entry, so the allowlist keeps meaning what it says."
    )

    blank = sorted(key for key, reason in ENV_ONLY_SETTINGS.items() if not reason.strip())
    assert not blank, f"ENV_ONLY_SETTINGS entries must carry a reason, not an empty string: {blank}"


def test_every_config_field_maps_to_a_real_settings_attribute() -> None:
    """A ConfigField pointing at nothing fails at request time, not at import."""
    attributes = _settings_attributes()
    dangling = sorted(field.key for field in CONFIG_FIELDS if field.attribute not in attributes)

    assert not dangling, f"ConfigField entries whose attribute is absent from Settings: {dangling}"


def test_every_registered_field_is_documented_in_env_example() -> None:
    """Admin-editable and undocumented is the mirror of the omission above."""
    documented = _env_example_keys()
    missing = sorted(field.key for field in CONFIG_FIELDS if field.key not in documented)

    assert not missing, (
        f"Registered settings absent from .env.example: {missing}. An operator reading the example "
        "file would not know these exist."
    )


def test_env_example_documents_no_unknown_keys() -> None:
    """Catches a key that was renamed in code but left behind in the example file."""
    managed = {field.key for field in CONFIG_FIELDS}
    unknown = sorted(_env_example_keys() - managed - UNMANAGED_ENV_EXAMPLE_KEYS)

    assert not unknown, (
        f".env.example documents keys no ConfigField owns: {unknown}. Either the setting was "
        "renamed or removed in code, or the key belongs in UNMANAGED_ENV_EXAMPLE_KEYS with a "
        "comment explaining why the registry must not own it."
    )
