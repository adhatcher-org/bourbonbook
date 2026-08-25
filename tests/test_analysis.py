from __future__ import annotations

import asyncio

import pytest

import bourbonbook.analysis
from bourbonbook.analysis import (
    ANALYSIS_STATUS_VALUES,
    OUTPUT_FIELDS,
    PHOTO_OUTPUT_FIELDS,
    PhotoAnalysisResult,
    _as_float,
    analysis_schema,
    analyze_bottle,
    analyze_bottle_name,
    enrich_from_verified_catalog,
    merge_analysis,
    normalize_analysis,
    reconcile_proof_and_abv,
    search_bottle_prices,
    warm_analysis_model,
)
from bourbonbook.config import Settings


def settings_for(tmp_path, provider: str) -> Settings:
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        session_secret="test-secret",
        secure_cookies=False,
        ollama_url="http://ollama.test",
        ollama_model="test-ollama",
        max_users=10,
        max_upload_mb=2,
        analysis_provider=provider,
        openai_api_key="test-key",
        openai_model="test-openai",
    )


def test_openai_provider_is_selected(tmp_path, monkeypatch) -> None:
    async def fake_request(prompt, settings, photo=None):
        assert "Example Bourbon" in prompt
        assert settings.openai_model == "test-openai"
        assert photo is None
        return {"proof": 114}, "complete"

    monkeypatch.setattr("bourbonbook.openai_provider.request_analysis", fake_request)

    result, status = asyncio.run(
        analyze_bottle_name("Example Bourbon", settings_for(tmp_path, "openai"))
    )

    assert status == "complete"
    assert result == {"name": "Example Bourbon", "proof": 114}


def test_unknown_provider_is_unavailable(tmp_path) -> None:
    result, status = asyncio.run(analyze_bottle_name("Example", settings_for(tmp_path, "other")))

    assert status == "unavailable"
    assert result == {}


def test_ollama_provider_and_price_provider_boundaries(tmp_path, monkeypatch) -> None:
    settings = settings_for(tmp_path, "ollama")

    async def fake_request(prompt, settings, photo=None):
        return {"name": "From Ollama", "photo": str(photo) if photo else None}, "complete"

    monkeypatch.setattr("bourbonbook.ollama.request_analysis", fake_request)
    photo_result = asyncio.run(analyze_bottle(tmp_path / "photo.jpg", settings))
    assert photo_result.values["name"] == "From Ollama"
    assert asyncio.run(analyze_bottle_name("Bottle", settings))[1] == "complete"

    async def fake_prices(name, settings, *, size=None):
        assert size == "750ml"
        return {"msrp": 50.0}, [], "complete"

    monkeypatch.setattr("bourbonbook.ollama_search.search_prices", fake_prices)
    assert asyncio.run(search_bottle_prices("Bottle", settings, size="750ml"))[0] == {"msrp": 50.0}


def test_openai_price_provider_is_selected(tmp_path, monkeypatch) -> None:
    settings = settings_for(tmp_path, "openai")

    async def fake_prices(name, settings, *, size=None):
        assert size == "750ml"
        return {"msrp": 42.0}, [], "complete"

    monkeypatch.setattr("bourbonbook.openai_provider.search_prices", fake_prices)
    assert asyncio.run(search_bottle_prices("Bottle", settings, size="750ml"))[0] == {"msrp": 42.0}


def test_unknown_price_provider_is_unavailable(tmp_path) -> None:
    settings = settings_for(tmp_path, "other")

    assert asyncio.run(search_bottle_prices("Bottle", settings)) == ({}, [], "unavailable")


def test_warm_analysis_model_only_dispatches_for_ollama(tmp_path, monkeypatch) -> None:
    calls: list[object] = []

    async def fake_warm(settings):
        calls.append(settings)

    monkeypatch.setattr("bourbonbook.ollama.warm_vision_model", fake_warm)

    ollama_settings = settings_for(tmp_path, "ollama")
    asyncio.run(warm_analysis_model(ollama_settings))
    assert calls == [ollama_settings]

    asyncio.run(warm_analysis_model(settings_for(tmp_path, "openai")))
    asyncio.run(warm_analysis_model(settings_for(tmp_path, "other")))
    assert calls == [ollama_settings]


def test_partial_ollama_photo_analysis_refines_with_text_model_only(tmp_path, monkeypatch) -> None:
    settings = settings_for(tmp_path, "ollama")
    calls: list[str] = []

    async def fake_ollama(prompt, configured_settings, photo=None):
        calls.append("vision" if photo else "text")
        return {
            "name": "Uncatalogued Bottle",
            "ocr_text": "UNCATALOGUED BOTTLE 100 PROOF",
            "proof": 100,
        }, "complete"

    monkeypatch.setattr("bourbonbook.ollama.request_analysis", fake_ollama)

    photo_result = asyncio.run(analyze_bottle(tmp_path / "photo.jpg", settings))
    values, status = photo_result.values, photo_result.status

    assert status == "complete"
    assert values["ocr_text"] == "UNCATALOGUED BOTTLE 100 PROOF"
    assert calls == ["vision", "text"]


def test_photo_bottled_date_is_exact_and_isolated_from_refinement_and_catalog(
    tmp_path, monkeypatch
) -> None:
    settings = settings_for(tmp_path, "ollama")

    async def fake_ollama(prompt, configured_settings, photo=None):
        if photo:
            return {"name": "Uncatalogued Bottle", "date_bottled": "2025-03-07"}, "complete"
        return {"date_bottled": "2024-01-01", "brand": "Refined"}, "complete"

    monkeypatch.setattr("bourbonbook.ollama.request_analysis", fake_ollama)

    result = asyncio.run(analyze_bottle(tmp_path / "photo.jpg", settings))

    assert isinstance(result, PhotoAnalysisResult)
    assert result.date_bottled.isoformat() == "2025-03-07"
    assert "date_bottled" not in result.values


def test_photo_bottled_date_rejects_partial_or_invalid_values(tmp_path, monkeypatch) -> None:
    settings = settings_for(tmp_path, "openai")

    async def fake_openai(prompt, configured_settings, photo=None):
        return {"name": "Example", "date_bottled": "2025-2-07"}, "complete"

    monkeypatch.setattr("bourbonbook.openai_provider.request_analysis", fake_openai)

    assert asyncio.run(analyze_bottle(tmp_path / "photo.jpg", settings)).date_bottled is None


def test_merge_analysis_drops_msrp_by_default() -> None:
    merged = merge_analysis({"name": "Example"}, {"msrp": 69.99, "brand": "Example Brand"})

    assert "msrp" not in merged
    assert merged["brand"] == "Example Brand"


def test_verified_catalog_match_carries_its_curated_msrp() -> None:
    values, matched = enrich_from_verified_catalog({"name": "New Riff 8 Years"})

    assert matched is True
    assert values["msrp"] == 69.99


def test_analyze_bottle_name_returns_verified_catalog_msrp(tmp_path) -> None:
    values, status = asyncio.run(
        analyze_bottle_name("New Riff 8 Years", settings_for(tmp_path, "ollama"))
    )

    assert status == "verified"
    assert values["msrp"] == 69.99


def test_normalize_analysis_derives_missing_abv_from_proof() -> None:
    values = normalize_analysis({"proof": 90})

    assert values["abv"] == 45.0


def test_normalize_analysis_derives_missing_proof_from_abv() -> None:
    values = normalize_analysis({"abv": 45.0})

    assert values["proof"] == 90.0


def test_normalize_analysis_resolves_a_disagreeing_proof_and_abv_to_the_higher_reading() -> None:
    values = normalize_analysis({"proof": 90, "abv": 43.0})

    assert values["proof"] == 90.0
    assert values["abv"] == 45.0


def test_normalize_analysis_leaves_a_consistent_proof_and_abv_untouched() -> None:
    values = normalize_analysis({"proof": 107.0, "abv": 53.5})

    assert values["proof"] == 107.0
    assert values["abv"] == 53.5


def test_normalize_analysis_reconciles_proof_and_abv_even_without_a_fill_level() -> None:
    """Proof/ABV reconciliation must not be skipped just because fill_level is absent,
    which is the normal case for a typed-name lookup rather than a photo."""
    values = normalize_analysis({"proof": 90})

    assert values["abv"] == 45.0
    assert "fill_level" not in values


def test_as_float_extracts_the_numeric_token_from_a_noisy_string() -> None:
    assert _as_float("107 proof") == 107.0
    assert _as_float("53.5% ABV") == 53.5


def test_as_float_returns_none_for_clearly_non_numeric_input() -> None:
    assert _as_float("not a number") is None
    assert _as_float(None) is None
    assert _as_float("") is None


def test_reconcile_proof_and_abv_derives_a_clean_proof_from_a_noisy_abv_string() -> None:
    """A noisy ABV like "53.5% ABV" used to fail to parse at all (the old ``_as_float``
    only stripped a trailing "%"), so reconcile bailed out entirely. With the regex fix,
    it still parses and a clean proof can be derived from it."""
    normalized = {"abv": "53.5% ABV"}

    reconcile_proof_and_abv(normalized)

    assert normalized["proof"] == 107.0


def test_normalize_analysis_snaps_size_to_the_nearest_standard_bottle() -> None:
    values = normalize_analysis({"size": "751ml"})

    assert values["size"] == "750ml"


def test_normalize_analysis_converts_size_units_before_snapping() -> None:
    values = normalize_analysis({"size": "1L"})

    assert values["size"] == "1000ml"


def test_normalize_analysis_leaves_a_non_standard_size_untouched() -> None:
    values = normalize_analysis({"size": "620ml"})

    assert values["size"] == "620ml"


# --- the analysis JSON Schema (A15) -------------------------------------------------

PHOTO_SCHEMA_PROPERTY_ORDER = [
    "name",
    "brand",
    "release",
    "edition",
    "spirit_type",
    "distilled_by",
    "mash_bill",
    "proof",
    "abv",
    "size",
    "age_statement",
    "barrel_number",
    "bottle_number",
    "warehouse",
    "floor",
    "status",
    "fill_level",
    "msrp",
    "date_bottled",
    "ocr_text",
]
NAME_SCHEMA_PROPERTY_ORDER = [
    name for name in PHOTO_SCHEMA_PROPERTY_ORDER if name != "date_bottled"
]
TEXTUAL_SCHEMA_FIELDS = (
    "name",
    "brand",
    "release",
    "edition",
    "spirit_type",
    "distilled_by",
    "mash_bill",
    "size",
    "age_statement",
    "barrel_number",
    "bottle_number",
    "warehouse",
    "floor",
    "date_bottled",
)


def test_analysis_schema_membership_matches_the_output_field_tuples() -> None:
    photo_schema = analysis_schema(photo=True)
    name_schema = analysis_schema(photo=False)

    assert set(photo_schema["properties"]) == set(PHOTO_OUTPUT_FIELDS)
    assert set(name_schema["properties"]) == set(OUTPUT_FIELDS)
    assert photo_schema["required"] == list(photo_schema["properties"])
    assert name_schema["required"] == list(name_schema["properties"])
    assert "date_bottled" not in name_schema["properties"]
    # ocr_text is emitted LAST. A degenerate repetition loop inside the free-text transcription
    # truncates the object; last position means the other fields are already emitted when it
    # starts. Measured 2026-08-23: ocr_text first produced 0/10 parseable responses, last 8/9.
    assert list(photo_schema["properties"])[-1] == "ocr_text"
    assert list(name_schema["properties"])[-1] == "ocr_text"
    assert list(photo_schema["properties"])[-2] == "date_bottled"


def test_analysis_schema_property_order_is_the_pinned_order() -> None:
    """Order is a design decision, so it is pinned as data rather than recomputed."""
    assert list(analysis_schema(photo=True)["properties"]) == PHOTO_SCHEMA_PROPERTY_ORDER
    assert list(analysis_schema(photo=False)["properties"]) == NAME_SCHEMA_PROPERTY_ORDER


def test_analysis_schema_types_are_nullable_and_msrp_is_null_only() -> None:
    schema = analysis_schema(photo=True)
    properties = schema["properties"]

    assert properties["proof"] == {"type": ["number", "null"]}
    assert properties["abv"] == {"type": ["number", "null"]}
    assert properties["fill_level"] == {"type": ["integer", "null"]}
    for field in TEXTUAL_SCHEMA_FIELDS:
        assert properties[field] == {"type": ["string", "null"]}, field
    assert properties["msrp"] == {"type": "null"}
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert analysis_schema(photo=False)["additionalProperties"] is False


def test_analysis_schema_ocr_text_is_nullable_on_photo_and_null_only_on_name() -> None:
    assert analysis_schema(photo=True)["properties"]["ocr_text"] == {"type": ["string", "null"]}
    assert analysis_schema(photo=False)["properties"]["ocr_text"] == {"type": "null"}


def test_analysis_schema_status_enum_matches_the_normalizer_vocabulary() -> None:
    enum = analysis_schema(photo=True)["properties"]["status"]["enum"]

    assert enum == ["Unopened", "Opened", "Empty", None]
    assigned = {
        normalize_analysis({"fill_level": 100})["status"],
        normalize_analysis({"fill_level": 50})["status"],
        normalize_analysis({"fill_level": 0})["status"],
    }
    assert set(enum) - {None} == set(ANALYSIS_STATUS_VALUES) == assigned


def test_analysis_schema_matches_the_golden_snapshot() -> None:
    """The one test that makes any schema change visible in review."""
    expected_photo = {
        "type": "object",
        "properties": {
            "name": {"type": ["string", "null"]},
            "brand": {"type": ["string", "null"]},
            "release": {"type": ["string", "null"]},
            "edition": {"type": ["string", "null"]},
            "spirit_type": {"type": ["string", "null"]},
            "distilled_by": {"type": ["string", "null"]},
            "mash_bill": {"type": ["string", "null"]},
            "proof": {"type": ["number", "null"]},
            "abv": {"type": ["number", "null"]},
            "size": {"type": ["string", "null"]},
            "age_statement": {"type": ["string", "null"]},
            "barrel_number": {"type": ["string", "null"]},
            "bottle_number": {"type": ["string", "null"]},
            "warehouse": {"type": ["string", "null"]},
            "floor": {"type": ["string", "null"]},
            "status": {
                "type": ["string", "null"],
                "enum": ["Unopened", "Opened", "Empty", None],
            },
            "fill_level": {"type": ["integer", "null"]},
            "msrp": {"type": "null"},
            "date_bottled": {"type": ["string", "null"]},
            "ocr_text": {"type": ["string", "null"]},
        },
        "required": PHOTO_SCHEMA_PROPERTY_ORDER,
        "additionalProperties": False,
    }
    expected_name = {
        "type": "object",
        "properties": {
            "name": {"type": ["string", "null"]},
            "brand": {"type": ["string", "null"]},
            "release": {"type": ["string", "null"]},
            "edition": {"type": ["string", "null"]},
            "spirit_type": {"type": ["string", "null"]},
            "distilled_by": {"type": ["string", "null"]},
            "mash_bill": {"type": ["string", "null"]},
            "proof": {"type": ["number", "null"]},
            "abv": {"type": ["number", "null"]},
            "size": {"type": ["string", "null"]},
            "age_statement": {"type": ["string", "null"]},
            "barrel_number": {"type": ["string", "null"]},
            "bottle_number": {"type": ["string", "null"]},
            "warehouse": {"type": ["string", "null"]},
            "floor": {"type": ["string", "null"]},
            "status": {
                "type": ["string", "null"],
                "enum": ["Unopened", "Opened", "Empty", None],
            },
            "fill_level": {"type": ["integer", "null"]},
            "msrp": {"type": "null"},
            "ocr_text": {"type": "null"},
        },
        "required": NAME_SCHEMA_PROPERTY_ORDER,
        "additionalProperties": False,
    }

    assert analysis_schema(photo=True) == expected_photo
    assert analysis_schema(photo=False) == expected_name
    assert list(analysis_schema(photo=True)["properties"]) == list(expected_photo["properties"])
    assert list(analysis_schema(photo=False)["properties"]) == list(expected_name["properties"])


def test_analysis_schema_fails_fast_on_field_set_drift(monkeypatch) -> None:
    """Called directly: `ollama.py` binds the tuple at import, so a patch cannot reach it."""
    specs = dict(bourbonbook.analysis.ANALYSIS_FIELD_SPECS)
    specs["orphan_field"] = {"type": ["string", "null"]}
    monkeypatch.setattr(bourbonbook.analysis, "ANALYSIS_FIELD_SPECS", specs)
    with pytest.raises(ValueError, match="orphan_field"):
        bourbonbook.analysis.analysis_schema(photo=True)
    monkeypatch.undo()

    monkeypatch.setattr(
        bourbonbook.analysis,
        "PHOTO_OUTPUT_FIELDS",
        bourbonbook.analysis.PHOTO_OUTPUT_FIELDS + ("unspecified_field",),
    )
    with pytest.raises(ValueError, match="unspecified_field"):
        bourbonbook.analysis.analysis_schema(photo=True)


def test_analysis_schema_returns_an_isolated_copy_per_call() -> None:
    first = analysis_schema(photo=True)
    first["properties"]["name"]["type"] = "mutated"
    first["properties"]["injected"] = {"type": "null"}
    first["required"].append("injected")

    second = analysis_schema(photo=True)

    assert second["properties"]["name"] == {"type": ["string", "null"]}
    assert "injected" not in second["properties"]
    assert "injected" not in second["required"]
    third = analysis_schema(photo=True)
    assert second["properties"] is not third["properties"]
    assert second["properties"]["status"] is not third["properties"]["status"]
    assert second["required"] is not third["required"]
