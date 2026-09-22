import io
import uuid
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import select

from core.db import get_session
from core.models import Column, Dataset, DatasetVersion, PendingTransform
from core.settings import get_settings
from core.storage import get_s3_client
from tools.pii import scan_column
from tools.profiler import coerce_types, profile_dataframe


class PendingTransformNotFound(Exception):
    pass


class PendingTransformNotPending(Exception):
    pass


class VersionNotFound(Exception):
    pass


def _pending_summary(p: PendingTransform) -> dict:
    return {
        "id": str(p.id),
        "dataset_id": str(p.dataset_id),
        "base_version_id": str(p.base_version_id),
        "kind": p.kind,
        "question": p.question,
        "diff_summary": p.diff_summary,
        "row_count_before": p.row_count_before,
        "row_count_after": p.row_count_after,
        "column_count_before": p.column_count_before,
        "column_count_after": p.column_count_after,
        "columns_added": p.columns_added,
        "columns_removed": p.columns_removed,
        "status": p.status,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


def _version_summary(v: DatasetVersion) -> dict:
    return {
        "id": str(v.id),
        "dataset_id": str(v.dataset_id),
        "version_number": v.version_number,
        "row_count": v.row_count,
        "column_count": v.column_count,
        "description": v.description,
        "source": v.source,
        "parent_version_id": str(v.parent_version_id) if v.parent_version_id else None,
        "diff_summary": v.diff_summary,
        "generated_code": v.generated_code,
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }


def confirm_transform(pending_id: str) -> dict:
    """Turns a proposed transform/join into a real, immutable DatasetVersion
    and repoints the dataset's active pointer at it. The pending parquet
    (already produced and validated at propose time) is copied to its
    permanent versioned key rather than re-executing anything - what the
    user approved in the preview is exactly what gets committed.
    """
    settings = get_settings()
    s3 = get_s3_client()

    with get_session() as session:
        pending = session.get(PendingTransform, uuid.UUID(pending_id))
        if pending is None:
            raise PendingTransformNotFound(f"No pending transform found with id {pending_id}")
        if pending.status != "pending":
            raise PendingTransformNotPending(f"Pending transform {pending_id} is already {pending.status}")

        dataset = session.get(Dataset, pending.dataset_id)
        max_version_number = session.scalar(
            select(DatasetVersion.version_number)
            .where(DatasetVersion.dataset_id == pending.dataset_id)
            .order_by(DatasetVersion.version_number.desc())
            .limit(1)
        )
        new_version_number = (max_version_number or 0) + 1
        parquet_key = f"datasets/{pending.dataset_id}/v{new_version_number}/data.parquet"

        obj = s3.get_object(Bucket=settings.s3_bucket, Key=pending.pending_parquet_key)
        parquet_bytes = obj["Body"].read()
        s3.put_object(Bucket=settings.s3_bucket, Key=parquet_key, Body=parquet_bytes)

        df = pd.read_parquet(io.BytesIO(parquet_bytes))
        profiles = profile_dataframe(df)
        for profile in profiles:
            is_pii, entities = scan_column(profile["name"], profile["sample_values"])
            profile["is_pii"] = is_pii
            profile["pii_entities"] = entities
            profile["description"] = None
        coerce_types(df, profiles)

        version_id = uuid.uuid4()
        version = DatasetVersion(
            id=version_id,
            dataset_id=pending.dataset_id,
            version_number=new_version_number,
            parquet_key=parquet_key,
            row_count=pending.row_count_after,
            column_count=pending.column_count_after,
            description=pending.diff_summary,
            source=pending.kind,
            parent_version_id=pending.base_version_id,
            generated_code=pending.generated_code,
            diff_summary=pending.diff_summary,
        )
        session.add(version)
        session.flush()

        session.add_all(
            Column(
                id=uuid.uuid4(),
                dataset_version_id=version_id,
                position=p["position"],
                name=p["name"],
                pandas_dtype=p["pandas_dtype"],
                inferred_type=p["inferred_type"],
                null_count=p["null_count"],
                null_pct=p["null_pct"],
                distinct_count=p["distinct_count"],
                cardinality_ratio=p["cardinality_ratio"],
                sample_values=p["sample_values"],
                is_pii=p["is_pii"],
                pii_entities=p["pii_entities"],
                description=p["description"],
            )
            for p in profiles
        )

        dataset.active_version_id = version_id
        pending.status = "confirmed"
        pending.resolved_at = datetime.now(timezone.utc)
        session.flush()
        result = _version_summary(version)

    s3.delete_object(Bucket=settings.s3_bucket, Key=pending.pending_parquet_key)
    return result


def reject_transform(pending_id: str) -> dict:
    settings = get_settings()
    s3 = get_s3_client()

    with get_session() as session:
        pending = session.get(PendingTransform, uuid.UUID(pending_id))
        if pending is None:
            raise PendingTransformNotFound(f"No pending transform found with id {pending_id}")
        if pending.status != "pending":
            raise PendingTransformNotPending(f"Pending transform {pending_id} is already {pending.status}")

        pending.status = "rejected"
        pending.resolved_at = datetime.now(timezone.utc)
        pending_key = pending.pending_parquet_key

    s3.delete_object(Bucket=settings.s3_bucket, Key=pending_key)
    return {"id": pending_id, "status": "rejected"}


def list_versions(dataset_id: str) -> dict:
    with get_session() as session:
        dataset = session.get(Dataset, uuid.UUID(dataset_id))
        if dataset is None:
            raise VersionNotFound(f"No dataset found with id {dataset_id}")
        versions = session.scalars(
            select(DatasetVersion)
            .where(DatasetVersion.dataset_id == dataset.id)
            .order_by(DatasetVersion.version_number.desc())
        ).all()
        return {
            "active_version_id": str(dataset.active_version_id) if dataset.active_version_id else None,
            "versions": [_version_summary(v) for v in versions],
        }


def set_active_version(dataset_id: str, version_id: str) -> dict:
    """Undo (and redo, and jumping to any arbitrary point in history) are all
    the same operation: repoint the active pointer. Nothing is deleted or
    recomputed, since every version's parquet already exists immutably.
    """
    with get_session() as session:
        dataset = session.get(Dataset, uuid.UUID(dataset_id))
        if dataset is None:
            raise VersionNotFound(f"No dataset found with id {dataset_id}")
        version = session.get(DatasetVersion, uuid.UUID(version_id))
        if version is None or version.dataset_id != dataset.id:
            raise VersionNotFound(f"No version {version_id} found for dataset {dataset_id}")

        dataset.active_version_id = version.id
        result = {"dataset_id": dataset_id, "active_version_id": version_id, "version_number": version.version_number}
    return result


_EXPORT_MIME_TYPES = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "parquet": "application/octet-stream",
}


def export_version(dataset_id: str, version_id: str | None, fmt: str) -> tuple[bytes, str, str]:
    if fmt not in _EXPORT_MIME_TYPES:
        raise ValueError(f"Unsupported export format '{fmt}' - use csv, xlsx, or parquet.")

    settings = get_settings()
    s3 = get_s3_client()

    with get_session() as session:
        dataset = session.get(Dataset, uuid.UUID(dataset_id))
        if dataset is None:
            raise VersionNotFound(f"No dataset found with id {dataset_id}")
        target_id = uuid.UUID(version_id) if version_id else dataset.active_version_id
        if target_id is None:
            raise VersionNotFound(f"Dataset {dataset_id} has no active version")
        version = session.get(DatasetVersion, target_id)
        if version is None or version.dataset_id != dataset.id:
            raise VersionNotFound(f"No version {version_id} found for dataset {dataset_id}")
        parquet_key = version.parquet_key
        version_number = version.version_number
        dataset_name = dataset.name

    obj = s3.get_object(Bucket=settings.s3_bucket, Key=parquet_key)
    parquet_bytes = obj["Body"].read()

    filename = f"{dataset_name}_v{version_number}.{fmt}"
    if fmt == "parquet":
        return parquet_bytes, filename, _EXPORT_MIME_TYPES[fmt]

    df = pd.read_parquet(io.BytesIO(parquet_bytes))
    buffer = io.BytesIO()
    if fmt == "csv":
        df.to_csv(buffer, index=False)
    else:
        df.to_excel(buffer, index=False, engine="openpyxl")
    return buffer.getvalue(), filename, _EXPORT_MIME_TYPES[fmt]
