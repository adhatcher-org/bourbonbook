"""Exact-product, source-grounded attribution cache and authority rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from bourbonbook.analysis import GroundedAttributions, GroundedFieldResult
from bourbonbook.models import Bottle, BottleAttributionProvenance, ProductAttributionFact

FIELDS = ("distilled_by", "mash_bill")
TTL = timedelta(days=365)
_PLACEHOLDERS = {"", "untitled bottle", "unknown", "n/a", "none"}
_VALUE_LIMITS = {"distilled_by": 180, "mash_bill": 240}


def product_attribution_key(bottle: Bottle) -> str | None:
    """Return an exact component-boundary identity, never a fuzzy or size key."""
    parts = []
    for field in ("brand", "name", "release", "edition", "spirit_type"):
        value = " ".join(str(getattr(bottle, field, "")).casefold().split())
        if value and value not in _PLACEHOLDERS:
            parts.append(f"{field}={value}")
    # A product must be identified by more than a generic type or a placeholder name.
    if not any(part.startswith(("brand=", "name=")) for part in parts):
        return None
    return "|".join(parts)[:500]


def canonical_public_url(value: str) -> str | None:
    parts = urlsplit(value.strip())
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return None
    host = parts.hostname.casefold()
    if host == "localhost" or host.endswith(".localhost"):
        return None
    # This component never fetches a URL. Reject literal private/link-local hosts regardless,
    # rather than attempting DNS in the request path and creating an SSRF/rebinding surface.
    import ipaddress

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (address.is_private or address.is_loopback or address.is_link_local):
        return None
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


def valid_result(field: str, result: GroundedFieldResult | None) -> GroundedFieldResult | None:
    if result is None or result.outcome == "no_evidence":
        return GroundedFieldResult("no_evidence") if result is not None else None
    if result.outcome != "resolved" or not result.value:
        return None
    value = " ".join(result.value.split())
    title = " ".join((result.title or "").split())
    basis = " ".join((result.basis or "").split())
    url = canonical_public_url(result.url or "")
    if not url or not title or not basis or len(value) > _VALUE_LIMITS[field]:
        return None
    if len(title) > 240 or len(basis) > 500:
        return None
    return GroundedFieldResult("resolved", value, title, url, basis)


def fact_is_fresh(fact: ProductAttributionFact, now: datetime | None = None) -> bool:
    checked_at = fact.checked_at
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=UTC)
    return checked_at >= (now or datetime.now(UTC)) - TTL


def provenance_for(bottle: Bottle, field: str) -> BottleAttributionProvenance | None:
    return next((item for item in bottle.attribution_provenance if item.field == field), None)


def set_provenance(
    bottle: Bottle, field: str, authority: str, fact: ProductAttributionFact | None = None
) -> None:
    existing = provenance_for(bottle, field)
    if existing is None:
        bottle.attribution_provenance.append(
            BottleAttributionProvenance(field=field, authority=authority, fact=fact)
        )
    else:
        existing.authority, existing.fact, existing.observed_at = authority, fact, datetime.now(UTC)


def apply_user_edits(bottle: Bottle, previous: dict[str, str]) -> None:
    for field in FIELDS:
        if previous[field] != getattr(bottle, field):
            set_provenance(bottle, field, "user_entered")


def _may_automate(bottle: Bottle, field: str) -> bool:
    provenance = provenance_for(bottle, field)
    if provenance and provenance.authority in {
        "user_entered",
        "legacy_unknown",
        "verified_catalog",
    }:
        return False
    return (
        not getattr(bottle, field)
        or provenance is None
        or provenance.authority == "provider_recall"
    )


def _apply_fact(bottle: Bottle, field: str, fact: ProductAttributionFact) -> bool:
    if not _may_automate(bottle, field):
        return False
    if fact.outcome == "resolved" and fact.value:
        setattr(bottle, field, fact.value)
        set_provenance(bottle, field, "grounded_web", fact)
        return True
    if (
        fact.outcome == "no_evidence"
        and provenance_for(bottle, field)
        and provenance_for(bottle, field).authority == "provider_recall"
    ):
        setattr(bottle, field, "")
        set_provenance(bottle, field, "grounded_web", fact)
        return True
    return False


async def resolve_attributions(
    session: Session,
    bottle: Bottle,
    settings: Any,
    *,
    provider_result: GroundedAttributions | None = None,
) -> set[str]:
    """Apply fresh cached evidence or a supplied/live provider result per field.

    ``provider_result`` lets deterministic callers inject a captured adapter result. Normal
    callers dispatch lazily only after cache misses.
    """
    key = product_attribution_key(bottle)
    if not key:
        return set()
    changed: set[str] = set()
    missing: list[str] = []
    for field in FIELDS:
        fact = session.scalar(
            select(ProductAttributionFact).where(
                ProductAttributionFact.product_key == key, ProductAttributionFact.field == field
            )
        )
        if fact is not None and fact_is_fresh(fact):
            if _apply_fact(bottle, field, fact):
                changed.add(field)
        elif _may_automate(bottle, field):
            missing.append(field)
    if not missing:
        return changed
    if provider_result is None:
        if settings.analysis_provider == "ollama":
            from bourbonbook.ollama_search import search_product_attributions

            provider_result = await search_product_attributions(key, settings)
        elif settings.analysis_provider == "openai":
            from bourbonbook.openai_provider import search_product_attributions

            provider_result = await search_product_attributions(key, settings)
        else:
            return changed
    for field in missing:
        result = valid_result(field, getattr(provider_result, field))
        if result is None:
            continue
        fact = session.scalar(
            select(ProductAttributionFact).where(
                ProductAttributionFact.product_key == key, ProductAttributionFact.field == field
            )
        )
        if fact is None:
            fact = ProductAttributionFact(
                product_key=key,
                field=field,
                value=result.value,
                outcome=result.outcome,
                title=result.title,
                url=result.url,
                basis=result.basis,
            )
            try:
                with session.begin_nested():
                    session.add(fact)
                    session.flush()
            except IntegrityError:
                fact = session.scalar(
                    select(ProductAttributionFact).where(
                        ProductAttributionFact.product_key == key,
                        ProductAttributionFact.field == field,
                    )
                )
                if fact is None:
                    continue
        else:
            fact.value = result.value
            fact.outcome = result.outcome
            fact.title = result.title
            fact.url = result.url
            fact.basis = result.basis
            fact.checked_at = datetime.now(UTC)
        if _apply_fact(bottle, field, fact):
            changed.add(field)
    return changed


def source_context(bottle: Bottle) -> dict[str, ProductAttributionFact]:
    return {
        item.field: item.fact
        for item in bottle.attribution_provenance
        if item.authority == "grounded_web" and item.fact is not None
    }
