"""Renders catalog metadata (never raw rows) into text safe to put in a prompt.

Only schema, descriptions, distinct counts, and the small sample_values already
captured during Phase 1 profiling ever reach this function - there is no path
here that reads the dataset's actual parquet/row data.
"""

from sqlalchemy.orm import selectinload

from core.db import get_session
from core.models import Dataset, DatasetVersion


class DatasetNotFound(Exception):
    pass


def get_active_dataset(dataset_id: str) -> tuple[Dataset, DatasetVersion]:
    with get_session() as session:
        dataset = session.get(
            Dataset, dataset_id, options=[selectinload(Dataset.versions).selectinload(DatasetVersion.columns)]
        )
        if dataset is None or dataset.active_version_id is None:
            raise DatasetNotFound(f"No dataset found with id {dataset_id}")
        version = next(v for v in dataset.versions if v.id == dataset.active_version_id)
        # Detach-safe: force-load what we need while the session is open.
        _ = [c.name for c in version.columns]
        return dataset, version


def build_schema_context(dataset_id: str) -> tuple[str, str]:
    """Returns (dataset_name, rendered_schema_text) for the given dataset."""
    dataset, version = get_active_dataset(dataset_id)

    lines = [f"Dataset name: {dataset.name}", f"Row count: {version.row_count}", "Columns:"]
    for column in sorted(version.columns, key=lambda c: c.position):
        pieces = [f"- {column.name} ({column.inferred_type})"]
        if column.description:
            pieces.append(f": {column.description}")
        pieces.append(f" [distinct={column.distinct_count}, null%={round(column.null_pct * 100, 1)}]")
        if column.is_pii:
            pieces.append(" [PII - do not echo raw values from this column]")
        elif column.sample_values:
            pieces.append(f" sample values: {column.sample_values[:5]}")
        lines.append("".join(pieces))

    return dataset.name, "\n".join(lines)


def get_column_profiles(dataset_id: str) -> dict[str, dict]:
    """Structured (not prompt-text) column metadata, for deterministic
    validation code (e.g. chart spec validation) rather than LLM prompts.
    """
    _, version = get_active_dataset(dataset_id)
    return {
        c.name: {"inferred_type": c.inferred_type, "distinct_count": c.distinct_count, "null_pct": c.null_pct}
        for c in version.columns
    }
