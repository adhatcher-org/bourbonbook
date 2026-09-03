"""Async, stage-tracked orchestration for the add-bottle pipeline.

Runs the same three stages `add_bottle` used to run synchronously and blocking (photo analysis,
catalog name-based enrichment, price lookup), but as a FastAPI `BackgroundTasks` job that commits
its `processing_stage` after each step so `GET /bottles/{id}/status` pollers see live progress.
This mirrors, at a much smaller scale, the durable-state-machine precedent already established by
`catalog_import_worker.py` / `catalog_imports.py` for catalog-price imports: extraction never
executes in the HTTP request path.

`analyze_bottle` is imported directly so tests can monkeypatch it as
`bourbonbook.bottle_processing.analyze_bottle`. The enrichment/pricing helpers
(`enrich_bottle_by_name`, `apply_user_purchase_price`, `refresh_prices`, `apply_analysis`,
`normalized_analysis_status`) still live in `bourbonbook.main`, unchanged -- they are also still
used synchronously by the existing `/bottles/{id}/analyze` route -- and are imported here lazily,
inside the function body, because `bourbonbook.main` imports this module at startup; a top-level
import here would be a circular import.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import StrEnum

from sqlalchemy import update
from sqlalchemy.orm import Session

from bourbonbook.analysis import analyze_bottle
from bourbonbook.config import Settings
from bourbonbook.logging_config import log_event
from bourbonbook.models import Bottle
from bourbonbook.observability import AIUsageRecorder, usage_context
from bourbonbook.qdrant_prices import QdrantPriceIndex

logger = logging.getLogger(__name__)


class BottleProcessingStage(StrEnum):
    IDLE = "idle"  # default; bottle never went through the async pipeline
    QUEUED = "queued"  # row created, BackgroundTasks callback scheduled
    ANALYZING = "analyzing"  # stage 1: analyze_bottle() (vision call)
    ENRICHING = "enriching"  # stage 2: enrich_bottle_by_name()
    PRICING = "pricing"  # stage 3: apply_user_purchase_price() / refresh_prices()
    ATTRIBUTING = "attributing"  # source-grounded producer/mash-bill evidence
    COMPLETE = "complete"  # pipeline finished (outcome quality is in analysis_status, not here)
    FAILED = "failed"  # unexpected exception, or orphaned by a server restart


IN_PROGRESS_STAGES = (
    BottleProcessingStage.QUEUED,
    BottleProcessingStage.ANALYZING,
    BottleProcessingStage.ENRICHING,
    BottleProcessingStage.PRICING,
    BottleProcessingStage.ATTRIBUTING,
)


async def run_add_bottle_pipeline(
    session_factory: Callable[[], Session],
    bottle_id: int,
    settings: Settings,
    price_index: QdrantPriceIndex | None,
    usage_recorder: AIUsageRecorder | None,
    user_id: int,
) -> None:
    """Runs stages 1-3 for one bottle, committing after each so pollers see live progress.

    Never raises: any unexpected exception is caught, persisted as
    ``processing_stage="failed"`` + ``processing_error``, and logged. FastAPI's
    ``BackgroundTasks`` execution is part of the ASGI response cycle (Starlette runs it via
    ``Response.__call__`` after the body is sent); an uncaught exception here would surface as a
    server error against a request that has already returned 202 to the client, and would strand
    the bottle at whatever stage it last committed.

    Each step opens its own session via ``session_factory()`` -- never the request's session,
    which is already closed by the time ``BackgroundTasks`` run.
    """
    from bourbonbook.main import (
        apply_analysis,
        apply_photo_bottled_date,
        apply_user_purchase_price,
        enrich_bottle_by_name,
        normalized_analysis_status,
        refresh_prices,
        resolve_attributions,
        set_provenance,
    )

    try:
        with session_factory() as session:
            bottle = session.get(Bottle, bottle_id)
            if bottle is None:
                return

            bottle.processing_stage = BottleProcessingStage.ANALYZING.value
            session.commit()

            if bottle.photo_name:
                with usage_context(usage_recorder, user_id):
                    photo_result = await analyze_bottle(
                        settings.data_dir / "uploads" / bottle.photo_name, settings
                    )
                    analysis, analysis_status = photo_result.values, photo_result.status
                    apply_photo_bottled_date(bottle, photo_result.date_bottled)
            else:
                analysis, analysis_status = {}, "unavailable"
            analysis_status = normalized_analysis_status(analysis_status)
            bottle.analysis_status = analysis_status
            recall_fields = apply_analysis(
                bottle, analysis, allow_msrp=analysis_status == "verified"
            )
            for field in {"distilled_by", "mash_bill"} & recall_fields:
                set_provenance(bottle, field, "provider_recall")

            bottle.processing_stage = BottleProcessingStage.ENRICHING.value
            session.commit()

            # Computed once, exactly like today's synchronous add_bottle: enrichment can touch
            # bottle.name, so re-deriving this guard after enrichment ran could change whether
            # pricing runs at all -- that would be a real behavior change, not a refactor.
            should_enrich_and_price = bool(bottle.name) and bottle.name != "Untitled bottle"

            if should_enrich_and_price:
                enrichment, enrichment_status = await enrich_bottle_by_name(
                    bottle, settings, allow_provider=False
                )
                enriched_fields = apply_analysis(
                    bottle, enrichment, allow_msrp=enrichment_status == "verified"
                )
                for field in {"distilled_by", "mash_bill"} & enriched_fields:
                    set_provenance(
                        bottle,
                        field,
                        "verified_catalog"
                        if enrichment_status == "verified"
                        else "provider_recall",
                    )
                if enrichment:
                    bottle.analysis_status = normalized_analysis_status(enrichment_status)

            bottle.processing_stage = BottleProcessingStage.ATTRIBUTING.value
            session.commit()

            if should_enrich_and_price:
                await resolve_attributions(session, bottle, settings)

            bottle.processing_stage = BottleProcessingStage.PRICING.value
            session.commit()

            if should_enrich_and_price:
                user_price_applied = await apply_user_purchase_price(session, bottle, price_index)
                if not user_price_applied:
                    with usage_context(usage_recorder, user_id):
                        price_status = await refresh_prices(
                            session, bottle, settings, price_index=price_index
                        )
                else:
                    price_status = "user_price"
                if price_status == "complete":
                    bottle.analysis_status = price_status

            bottle.processing_stage = BottleProcessingStage.COMPLETE.value
            session.commit()
    except Exception as exc:  # noqa: BLE001 - genuinely must never propagate, see docstring
        log_event(
            logger,
            logging.ERROR,
            "bottle_processing_failed",
            "Add-bottle background pipeline failed",
            bottle_id=bottle_id,
            error_type=exc.__class__.__name__,
            exc_info=exc,
        )
        with session_factory() as session:
            bottle = session.get(Bottle, bottle_id)
            if bottle is not None:
                bottle.processing_stage = BottleProcessingStage.FAILED.value
                bottle.processing_error = repr(exc)[:2000]
                session.commit()


def recover_orphaned_bottle_processing(session: Session) -> int:
    """Startup-only sweep: any row still mid-pipeline at process start was abandoned by the
    previous process. A single-worker deployment means a restart is an unambiguous signal that
    the work is orphaned -- no lease/TTL bookkeeping is needed to tell "still running" apart from
    "abandoned" the way CatalogImportWorker's lease design has to.
    """
    result = session.execute(
        update(Bottle)
        .where(Bottle.processing_stage.in_([stage.value for stage in IN_PROGRESS_STAGES]))
        .values(
            processing_stage=BottleProcessingStage.FAILED.value,
            processing_error="Interrupted by server restart",
        )
    )
    return int(result.rowcount or 0)
