from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bourbonbook.catalog_imports import CatalogImportState, transition_batch
from bourbonbook.models import CatalogImportBatch


def test_transition_batch_records_applied_time_and_clears_lease() -> None:
    batch = CatalogImportBatch(state=CatalogImportState.REVIEW.value)
    batch.lease_expires_at = datetime(2026, 7, 22, 14, tzinfo=UTC)
    transitioned_at = datetime(2026, 7, 22, 13, tzinfo=UTC)

    transition_batch(batch, CatalogImportState.APPLIED, now=lambda: transitioned_at)

    assert batch.state == CatalogImportState.APPLIED.value
    assert batch.updated_at == transitioned_at
    assert batch.applied_at == transitioned_at
    assert batch.lease_expires_at is None


def test_transition_batch_allows_recovery_but_rejects_terminal_changes() -> None:
    batch = CatalogImportBatch(state=CatalogImportState.EXTRACTING.value)
    transition_batch(batch, CatalogImportState.QUEUED)
    assert batch.state == CatalogImportState.QUEUED.value

    batch.state = CatalogImportState.APPLIED.value
    with pytest.raises(ValueError, match="Cannot transition"):
        transition_batch(batch, CatalogImportState.QUEUED)
