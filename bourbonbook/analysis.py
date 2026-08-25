from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from bourbonbook.catalog import verified_product, verified_product_from_text
from bourbonbook.config import Settings


@dataclass(frozen=True)
class GroundedFieldResult:
    """One independently validated attribution result from recorded search evidence."""

    outcome: str
    value: str | None = None
    title: str | None = None
    url: str | None = None
    basis: str | None = None


@dataclass(frozen=True)
class GroundedAttributions:
    distilled_by: GroundedFieldResult | None = None
    mash_bill: GroundedFieldResult | None = None


STANDARD_SIZES_ML = (50, 200, 375, 750, 1000, 1750)
SIZE_SNAP_TOLERANCE_ML = 15
PROOF_ABV_TOLERANCE = 1.0
SIZE_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(ml|millilit(?:er|re)s?|cl|l|lit(?:er|re)s?)")

FIELDS = (
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
)
OUTPUT_FIELDS = FIELDS + ("ocr_text",)
PHOTO_OUTPUT_FIELDS = OUTPUT_FIELDS + ("date_bottled",)
PHOTO_BOTTLED_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MISSING_FIELDS = (
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
    "status",
    "fill_level",
)

# The single spirit-type vocabulary. The edit form renders these options and the analysis schema
# constrains the model to them, so an extraction cannot produce a value the form cannot display.
# "Rye Whiskey" rather than "Rye": the model reaches for the full phrase unprompted, and the longer
# name also matches the free-text "rye whiskey" already stored by earlier extractions.
SPIRIT_TYPES = (
    "Bourbon",
    "Rye Whiskey",
    "American Whiskey",
    "Canadian Whiskey",
    "Scotch",
    "Irish Whiskey",
    "Japanese Whisky",
    "Other",
)
ANALYSIS_STATUS_VALUES = ("Unopened", "Opened", "Empty")
# JSON Schema specification per output field. Membership is the PHOTO_OUTPUT_FIELDS superset;
# the name path selects a subset and overrides ocr_text only. Shape is constrained here, never
# truth: no pattern, minimum, maximum, format, or business range belongs in these specifications.
ANALYSIS_FIELD_SPECS: dict[str, dict[str, Any]] = {
    "name": {"type": ["string", "null"]},
    "brand": {"type": ["string", "null"]},
    "release": {"type": ["string", "null"]},
    "edition": {"type": ["string", "null"]},
    "spirit_type": {"type": ["string", "null"], "enum": [*SPIRIT_TYPES, None]},
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
    "status": {"type": ["string", "null"], "enum": [*ANALYSIS_STATUS_VALUES, None]},
    "fill_level": {"type": ["integer", "null"]},
    "msrp": {"type": "null"},
    "ocr_text": {"type": ["string", "null"]},
    "date_bottled": {"type": ["string", "null"]},
}
# Emission order, deliberately not tuple order: an all-required object is generated as a fixed
# sequence and the model cannot stop before the last property, so where the free-text
# transcription sits decides what survives a degenerate generation.
#
# ocr_text was placed FIRST until 2026-08-23, on the reasoning that the transcription should be
# generated before the fields it informs. Measurement against Ollama 0.32.13 / qwen3-vl:8b
# reversed that: the model reliably falls into a newline repetition loop inside the ocr_text
# string, the grammar cannot break a loop inside a legal string value, and the server aborts the
# generation mid-string. With ocr_text first that destroys the whole object.
#
#   ocr_text first: 0/10 runs produced parseable JSON (done=false, truncated ~350 chars)
#   ocr_text last:  8/9 runs produced all 20 keys, valid, done_reason="stop", ~3.3s
#
# Last position does not prevent the loop; it means the other 19 fields are already emitted
# before it can start. Do not move this back without re-running that comparison.
ANALYSIS_SCHEMA_FIELD_ORDER = tuple(
    field for field in PHOTO_OUTPUT_FIELDS if field != "ocr_text"
) + ("ocr_text",)
# A name-only call has no image, so any non-null ocr_text from it is by construction a
# hallucination -- and a hallucinated transcription is a live route to a false verified-catalog
# match that writes an MSRP. The property stays present, to keep membership equal to the output
# tuple and aligned with the prompt key list, but it can only be null.
NAME_PATH_OCR_TEXT_SPEC: dict[str, Any] = {"type": "null"}


def _validate_analysis_field_specs() -> None:
    """Fail fast when the output tuples and the schema specifications drift apart.

    Called at import of this module -- where a developer error belongs, and where it cannot
    reach an HTTP handler -- and again as the first statement of :func:`analysis_schema`, which
    is the only call site a test monkeypatch can reach. Both globals are read from the module,
    never from arguments, so a patched global is seen on the next call.
    """
    specified = set(ANALYSIS_FIELD_SPECS)
    declared = set(PHOTO_OUTPUT_FIELDS)
    missing = sorted(declared - specified)
    if missing:
        raise ValueError(
            "ANALYSIS_FIELD_SPECS has no JSON Schema specification for: " + ", ".join(missing)
        )
    extra = sorted(specified - declared)
    if extra:
        raise ValueError(
            "ANALYSIS_FIELD_SPECS specifies fields absent from PHOTO_OUTPUT_FIELDS: "
            + ", ".join(extra)
        )


class SchemaConformanceError(ValueError):
    """A parsed provider response did not conform to the schema that was sent with the request."""


def validate_against_schema(parsed: Any, schema: dict[str, Any]) -> None:
    """Verify a parsed response actually conforms to the schema the request carried.

    Sending a schema is not the same as receiving a constrained response. Measured against
    Ollama 0.32.13 on 2026-08-23: a generation aborted by the server (``done`` false) or stopped
    by context exhaustion (``done_reason`` "length") returns a truncated or non-conforming object,
    and ``maxLength`` is silently dropped by the grammar converter -- so individual keywords are
    not guaranteed to have been applied either. Verifying the result here turns an assumed
    guarantee into a checked one, and does so independently of which response channel carried it.
    """
    if not isinstance(parsed, dict):
        raise SchemaConformanceError(f"expected a JSON object, got {type(parsed).__name__}")
    properties: dict[str, Any] = schema.get("properties", {})
    missing = sorted(set(properties) - set(parsed))
    if missing:
        raise SchemaConformanceError("response is missing required keys: " + ", ".join(missing))
    extra = sorted(set(parsed) - set(properties))
    if extra:
        raise SchemaConformanceError("response carries unspecified keys: " + ", ".join(extra))
    for field, spec in properties.items():
        value = parsed[field]
        allowed = spec.get("type")
        allowed = [allowed] if isinstance(allowed, str) else list(allowed or ())
        if not _matches_json_type(value, allowed):
            raise SchemaConformanceError(
                f"{field} is {type(value).__name__}, schema allows {'/'.join(allowed)}"
            )
        choices = spec.get("enum")
        if choices is not None and value not in choices:
            raise SchemaConformanceError(f"{field} value {value!r} is outside the schema enum")


def _matches_json_type(value: Any, allowed: list[str]) -> bool:
    if not allowed:
        return True
    if value is None:
        return "null" in allowed
    if isinstance(value, bool):
        # JSON Schema does not treat a boolean as a number; neither do we.
        return "boolean" in allowed
    if isinstance(value, int):
        return bool({"integer", "number"} & set(allowed))
    if isinstance(value, float):
        return "number" in allowed or ("integer" in allowed and value.is_integer())
    if isinstance(value, str):
        return "string" in allowed
    if isinstance(value, list):
        return "array" in allowed
    if isinstance(value, dict):
        return "object" in allowed
    return False


def analysis_schema(*, photo: bool) -> dict[str, Any]:
    """Return the JSON Schema constraining one bottle-analysis response.

    Every property is required and nullable: "unknown" is expressed as null, not by omission.
    The returned tree is freshly assembled and deeply isolated, so a caller may mutate it.
    """
    _validate_analysis_field_specs()
    selected = set(PHOTO_OUTPUT_FIELDS if photo else OUTPUT_FIELDS)
    properties: dict[str, Any] = {}
    for field in ANALYSIS_SCHEMA_FIELD_ORDER:
        if field not in selected:
            continue
        if field == "ocr_text" and not photo:
            properties[field] = copy.deepcopy(NAME_PATH_OCR_TEXT_SPEC)
        else:
            properties[field] = copy.deepcopy(ANALYSIS_FIELD_SPECS[field])
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_validate_analysis_field_specs()

PHOTO_PROMPT = """You are a meticulous American-whiskey bottle archivist. Inspect the entire image,
including the neck label, main label, small-print proof/ABV line, handwritten barrel tag, and the
visible liquid level.

Field rules:
- name is the concise full product name and must include the expression when visible, such as
  "Weller Full Proof", "Weller Antique 107", or "Blanton's Straight From The Barrel".
- brand is only the brand. release is the named expression (Full Proof, Antique 107, Straight From
  The Barrel, The Original Single Barrel). edition is a batch, vintage, store pick, or single-barrel
  designation. Never put a dumped/bottled date in release or edition.
- proof and ABV must come from the label's alcohol line, not barrel numbers, dates, age statements,
  or fill level. Proof must equal exactly 2 × ABV. Re-read the line if they disagree.
- size is only package volume such as 750ml, never an age statement.
- On a barrel tag, map text beside "Barrel No", "Bottle No", "Warehouse", "Floor", or "Rick No"
  to the corresponding field. Do not shift values between fields.
- date_bottled is the bottling date printed on this specific bottle or its barrel tag. Return it
  only when the complete calendar date is clearly readable, using exactly YYYY-MM-DD. Return null
  for partial, ambiguous, inferred, or unreadable dates.
- Determine condition from the liquid boundary, not from whether a cap or seal is present. If amber
  liquid visibly continues through the shoulder and into the narrow neck with no meniscus in the
  wide body, the bottle is full: fill_level 100 and status Unopened. If a horizontal air/liquid
  boundary is visible in the shoulder or wide body, status is Opened and fill_level is the estimated
  percentage of the bottle's total capacity, rounded to the nearest 5. A meniscus near the middle of
  the body is roughly 40-50%, not 85%. At 0%, status is Empty. Status must agree with fill_level.
- Bottle-shape calibration matters. On a squat faceted Blanton's bottle, a liquid line near the top
  edge of the wide wraparound label is about 40%; it is not 85%. A full Blanton's has liquid through
  the rounded shoulder into the neck. On a tall cylindrical bottle, a line near mid-label is about
  50%.
- If the exact product identity is unambiguous, established product knowledge may supply
  distilled_by and a general mash_bill such as "wheated bourbon". Never invent exact percentages.
- Transcribe every readable bit of label text into ocr_text, preserving line breaks and
  small-print details when possible.
- MSRP must always be null; a photograph cannot establish current pricing.
- Use null for every uncertain or unreadable value. Numeric proof, ABV, and fill_level must not
  include symbols or units."""


@dataclass(frozen=True)
class PhotoAnalysisResult:
    """Photo fields plus the separately-provenanced optional bottled date."""

    values: dict[str, Any]
    status: str
    date_bottled: date | None


def name_prompt(name: str) -> str:
    return f"""Identify the whiskey product named {name!r}. Use null when a value is unknown or
varies by bottle. Numeric proof and ABV must not include symbols or units. Do not invent
barrel-specific information, mash-bill percentages, or facts you are not highly confident about.
This is an ungrounded lookup, so MSRP must always be null."""


def canonical_url(value: str) -> str:
    parts = urlsplit(value)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


def price_search_prompt(name: str, *, size: str | None = None) -> str:
    size_requirement = f" in the {size!r} bottle size" if size else ""
    product = f"the exact whiskey {name!r}{size_requirement}"
    return f"""Research the current Ohio retail price for {product}.

Search OHLQ.com first and use its Sizes & Pricing value when an exact product and bottle-size match
is available. When a bottle size is supplied, reject prices for every other size. Treat that Ohio
retail price as MSRP for this collection. If OHLQ is inaccessible or
has no exact match, broaden the web search and use the producer, another official state price book,
or a reputable whiskey publication.
Do not use retailer asking prices, search snippets, Reddit estimates, secondary-market prices, or
an edition/store pick that does not exactly match. Use a single USD value rather than a range.
Return null when reliable evidence is unavailable or conflicting. Select one best direct source;
its title and URL must come from the web results. Keep the basis to one short sentence in plain text
without Markdown."""


def missing_fields(values: dict[str, Any]) -> list[str]:
    return [field for field in MISSING_FIELDS if values.get(field) in (None, "")]


def merge_analysis(
    base: dict[str, Any], extra: dict[str, Any], *, allow_msrp: bool = False
) -> dict[str, Any]:
    merged = dict(base)
    for key, value in extra.items():
        if key == "msrp" and not allow_msrp:
            continue
        if value in (None, ""):
            continue
        if merged.get(key) in (None, ""):
            merged[key] = value
    return merged


def enrich_from_verified_catalog(values: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    match = None
    for candidate in (values.get("name"), values.get("ocr_text")):
        if candidate:
            match = verified_product(candidate) or verified_product_from_text(candidate)
        if match:
            break
    if not match:
        return values, False
    return merge_analysis(values, match, allow_msrp=True), True


def analysis_prompt(values: dict[str, Any], *, source: str) -> str:
    known = {
        key: value
        for key, value in values.items()
        if key in OUTPUT_FIELDS and value not in (None, "")
    }
    return f"""Identify this exact whiskey product, then fill any field below that is still missing.

You are identifying a product, not transcribing a label. Facts such as the producing distillery
and the general mash bill are almost never printed on a bottle -- supply them from established
product knowledge when, and only when, you are certain which product this is.

Field rules:
- distilled_by is the company or distillery that actually produces this whiskey. Return null unless
  you specifically know this product's producer. Do not derive one, and do not substitute a
  distillery that merely seems likely: an empty field is recoverable, a wrong one is not.
- mash_bill is the grain recipe in general terms, and it must agree with the spirit type: a rye
  whiskey is not wheated. Return null unless you know this specific product's recipe. Never invent
  percentages.
- Do not change any field already present in the JSON below.
- Do not invent pricing. MSRP must stay null.
- A null is the correct answer whenever you are not certain. A plausible guess is worse than an
  empty field here, because nothing downstream can tell the two apart.

The {source} is provided as context, but a fact absent from it is not therefore unknown.

Known values:
{json.dumps(known, indent=2, sort_keys=True, default=str)}

Return only JSON with these keys: {", ".join(OUTPUT_FIELDS)}."""


def _as_float(value: Any) -> float | None:
    try:
        return float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        pass
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    if match is None:
        return None
    try:
        return float(match.group())
    except ValueError:
        return None


def reconcile_proof_and_abv(normalized: dict[str, Any]) -> None:
    """Derive a missing proof/ABV from the other, or resolve a mismatch deterministically.

    Proof is defined as exactly 2 x ABV, so the two fields never need to be independently
    guessed once one is known. When both are present but disagree beyond OCR/transcription
    noise, trust whichever value implies the higher proof: a dropped or misread digit is far
    more likely to understate a value than invent an extra one.
    """
    proof, abv = _as_float(normalized.get("proof")), _as_float(normalized.get("abv"))
    if proof is None and abv is None:
        return
    if proof is None:
        normalized["proof"] = round(abv * 2, 1)
        return
    if abv is None:
        normalized["abv"] = round(proof / 2, 1)
        return
    if abs(proof - abv * 2) > PROOF_ABV_TOLERANCE:
        winning_proof = max(proof, abv * 2)
        normalized["proof"] = round(winning_proof, 1)
        normalized["abv"] = round(winning_proof / 2, 1)


def snap_size(normalized: dict[str, Any]) -> None:
    """Snap a recognized bottle size to the nearest standard US spirits size.

    Sizes are read from a printed volume, not estimated, so small transcription noise around
    an obviously-standard bottle (e.g. 751ml) should resolve to the real packaged size (750ml)
    rather than being scored/stored as a one-off value.
    """
    size = normalized.get("size")
    if not size:
        return
    match = SIZE_PATTERN.fullmatch(str(size).strip().lower())
    if not match:
        return
    amount, unit = float(match.group(1)), match.group(2)
    multiplier = 1 if unit.startswith(("ml", "millil")) else 10 if unit == "cl" else 1000
    millilitres = amount * multiplier
    nearest = min(STANDARD_SIZES_ML, key=lambda candidate: abs(candidate - millilitres))
    if abs(nearest - millilitres) <= SIZE_SNAP_TOLERANCE_ML:
        normalized["size"] = f"{nearest}ml"


_PRODUCER_SUFFIXES = (
    "distillery",
    "distilleries",
    "distilling",
    "distilling co",
    "distilling company",
    "distillery co",
    "distillery company",
    "distillers",
    "distillery inc",
)
_WHEATED_MARKERS = ("wheat", "wheated")
_RYE_MARKERS = ("rye",)


def _brand_echo(value: str, other: str) -> bool:
    """True when ``value`` is ``other`` with a producer word bolted on."""
    stripped = value.strip().lower()
    other = other.strip().lower()
    if not stripped or not other or stripped == other:
        return stripped == other and bool(stripped)
    return any(stripped == f"{other} {suffix}" for suffix in _PRODUCER_SUFFIXES)


def implausible_distiller(distilled_by: Any, values: dict[str, Any]) -> bool:
    """Reject a producer that is merely the brand with "Distillery" appended.

    Measured failure mode, 2026-08: the model returned "Elijah Craig Distillery" (Heaven Hill),
    "E.H. Taylor Distillery" (Buffalo Trace) and "Buffalo Trace Distillery" for a Wild Turkey
    product. Brand-echo is the single most common shape, it is always wrong when the brand is
    owned by a differently-named producer, and unlike a wrong-but-real answer it is detectable
    without a reference source.
    """
    if not isinstance(distilled_by, str) or not distilled_by.strip():
        return False
    for key in ("brand", "release", "name"):
        candidate = values.get(key)
        if isinstance(candidate, str) and _brand_echo(distilled_by, candidate):
            return True
    return False


def implausible_mash_bill(mash_bill: Any, values: dict[str, Any]) -> bool:
    """Reject a mash bill that contradicts the product's own spirit type.

    A wheated mash bill replaces rye as the flavouring grain, so "wheated" and a rye whiskey are
    mutually exclusive. The model produced exactly that contradiction ("wheated rye whiskey") and
    reached for "wheated bourbon" on three separate rye-recipe bourbons.
    """
    if not isinstance(mash_bill, str) or not mash_bill.strip():
        return False
    claim = mash_bill.strip().lower()
    context = " ".join(
        str(values.get(key) or "") for key in ("spirit_type", "name", "release")
    ).lower()
    wheated_claim = any(marker in claim for marker in _WHEATED_MARKERS)
    rye_product = any(marker in context for marker in _RYE_MARKERS)
    return wheated_claim and rye_product


def drop_implausible_attributions(values: dict[str, Any]) -> None:
    """Null out producer/mash-bill values a deterministic check can prove untrustworthy.

    This runs on provider output only. Catalog enrichment merges afterwards and is unaffected, so
    a verified product can still supply either field.
    """
    if implausible_distiller(values.get("distilled_by"), values):
        values["distilled_by"] = None
    if implausible_mash_bill(values.get("mash_bill"), values):
        values["mash_bill"] = None


def normalize_analysis(values: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(values)
    reconcile_proof_and_abv(normalized)
    snap_size(normalized)
    drop_implausible_attributions(normalized)
    fill_level = normalized.get("fill_level")
    try:
        fill = max(0, min(100, int(round(float(str(fill_level).rstrip("%"))))))
    except (TypeError, ValueError):
        return normalized
    if fill >= 90:
        normalized["fill_level"] = 100
        normalized["status"] = "Unopened"
    elif fill == 0:
        normalized["fill_level"] = 0
        normalized["status"] = "Empty"
    else:
        normalized["fill_level"] = fill
        normalized["status"] = "Opened"
    return normalized


def normalize_photo_bottled_date(value: Any) -> date | None:
    """Accept only an exact, complete photo-proposed ISO calendar date."""
    if not isinstance(value, str) or not PHOTO_BOTTLED_DATE_PATTERN.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


async def _request_provider_analysis(
    prompt: str, settings: Settings, photo: Path | None = None
) -> tuple[dict[str, Any], str]:
    if settings.analysis_provider == "openai":
        from bourbonbook.openai_provider import request_analysis

        return await request_analysis(prompt, settings, photo)
    if settings.analysis_provider == "ollama":
        from bourbonbook.ollama import request_analysis

        return await request_analysis(prompt, settings, photo)
    return {}, "unavailable"


async def _refine_analysis(
    values: dict[str, Any], settings: Settings, *, source: str
) -> tuple[dict[str, Any], str]:
    prompt = analysis_prompt(values, source=source)
    refined, status = await _request_provider_analysis(prompt, settings)
    values = merge_analysis(values, refined)
    values, matched = enrich_from_verified_catalog(values)
    if matched or not missing_fields(values):
        return values, "verified" if matched else "complete"
    return values, status


async def analyze_bottle(photo: Path, settings: Settings) -> PhotoAnalysisResult:
    values, status = await _request_provider_analysis(PHOTO_PROMPT, settings, photo)
    photo_bottled_date = normalize_photo_bottled_date(values.get("date_bottled"))
    values = {key: value for key, value in values.items() if key != "date_bottled"}
    if not values:
        return PhotoAnalysisResult(values, status, photo_bottled_date)
    values, matched = enrich_from_verified_catalog(values)
    if matched:
        return PhotoAnalysisResult(values, "verified", photo_bottled_date)
    if settings.analysis_provider == "ollama" and missing_fields(values):
        values, status = await _refine_analysis(
            values, settings, source="transcribed bottle-label text"
        )
        values.pop("date_bottled", None)
    return PhotoAnalysisResult(values, status, photo_bottled_date)


async def analyze_bottle_name(name: str, settings: Settings) -> tuple[dict[str, Any], str]:
    values, matched = enrich_from_verified_catalog({"name": name})
    if matched:
        return values, "verified"
    analyzed, status = await _request_provider_analysis(name_prompt(name), settings)
    if not analyzed:
        return {}, status
    values = merge_analysis(values, analyzed)
    values, matched = enrich_from_verified_catalog(values)
    if matched:
        return values, "verified"
    if values and settings.analysis_provider == "ollama" and missing_fields(values):
        return await _refine_analysis(values, settings, source="known bottle name")
    return values, status


async def search_bottle_prices(
    name: str, settings: Settings, *, size: str | None = None
) -> tuple[dict[str, float], list[dict[str, str]], str]:
    if settings.analysis_provider == "openai":
        from bourbonbook.openai_provider import search_prices

        return await search_prices(name, settings, size=size)
    if settings.analysis_provider == "ollama":
        from bourbonbook.ollama_search import search_prices

        return await search_prices(name, settings, size=size)
    return {}, [], "unavailable"


async def warm_analysis_model(settings: Settings) -> None:
    """Best-effort pre-load of the vision model for providers with a real load cost.

    Only Ollama evicts and reloads a model between requests; OpenAI is a remote API with no
    load step to hide.
    """
    if settings.analysis_provider == "ollama":
        from bourbonbook.ollama import warm_vision_model

        await warm_vision_model(settings)
