"""Renders catalog metadata (never raw rows) into text safe to put in a prompt.

Only schema, descriptions, distinct counts, and the small sample_values already
captured during Phase 1 profiling ever reach this function - there is no path
here that reads the dataset's actual parquet/row data.
"""

from sqlalchemy import select
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


def list_other_datasets_brief(exclude_dataset_id: str, limit: int = 20) -> str:
    """Compact 'name: col1, col2, ...' listing of other datasets, for a join
    request to reference by name. Column names only (no types/descriptions)
    to keep this cheap even with many candidate datasets.
    """
    with get_session() as session:
        datasets = session.scalars(
            select(Dataset)
            .where(Dataset.id != exclude_dataset_id)
            .options(selectinload(Dataset.versions).selectinload(DatasetVersion.columns))
            .order_by(Dataset.created_at.desc())
            .limit(limit)
        ).all()
        lines = []
        for dataset in datasets:
            version = next((v for v in dataset.versions if v.id == dataset.active_version_id), None)
            if not version:
                continue
            columns = ", ".join(sorted(c.name for c in version.columns))
            lines.append(f"- {dataset.name}: {columns}")
        return "\n".join(lines) if lines else "(no other datasets available)"


def describe_datasets_brief(dataset_ids: list[str]) -> str:
    """Compact 'id/name: columns' listing for exactly the given datasets -
    used to let the agent pick which attached dataset a question targets
    when a session has more than one, without paying for each one's full
    profile detail (that only gets loaded for the one actually resolved).
    """
    with get_session() as session:
        datasets = session.scalars(
            select(Dataset)
            .where(Dataset.id.in_(dataset_ids))
            .options(selectinload(Dataset.versions).selectinload(DatasetVersion.columns))
        ).all()
        lines = []
        for dataset in datasets:
            version = next((v for v in dataset.versions if v.id == dataset.active_version_id), None)
            if not version:
                continue
            columns = ", ".join(sorted(c.name for c in version.columns))
            lines.append(f"- id={dataset.id}, name={dataset.name}: {columns}")
        return "\n".join(lines) if lines else "(no datasets attached)"


def find_dataset_by_name(name_hint: str, exclude_dataset_id: str) -> Dataset | None:
    """Case-insensitive match against dataset names - the join spec prompt
    asks the LLM to copy a name verbatim from list_other_datasets_brief, but
    matches loosely (exact first, then substring) since it may not be exact.
    """
    with get_session() as session:
        exact = session.scalars(
            select(Dataset).where(Dataset.id != exclude_dataset_id, Dataset.name.ilike(name_hint))
        ).first()
        if exact:
            return exact
        return session.scalars(
            select(Dataset)
            .where(Dataset.id != exclude_dataset_id, Dataset.name.ilike(f"%{name_hint}%"))
            .order_by(Dataset.created_at.desc())
        ).first()


def get_column_profiles(dataset_id: str) -> dict[str, dict]:
    """Structured (not prompt-text) column metadata, for deterministic
    validation code (e.g. chart spec validation) rather than LLM prompts.
    """
    _, version = get_active_dataset(dataset_id)
    return {
        c.name: {"inferred_type": c.inferred_type, "distinct_count": c.distinct_count, "null_pct": c.null_pct}
        for c in version.columns
    }
