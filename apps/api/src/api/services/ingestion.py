import hashlib
import io
import uuid

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from api.services.users import get_or_create_user
from core.db import get_session
from core.models import Column, Dataset, DatasetVersion
from core.settings import get_settings
from core.storage import get_s3_client
from tools.excel import load_tabular
from tools.pii import scan_column
from tools.profiler import ALLOWED_TYPES, coerce_types, is_plausible_override, profile_dataframe

try:
    from agent.schema_review import review_columns
except Exception:  # pragma: no cover - LLM review is best-effort, never blocks ingestion
    review_columns = None


def ingest_file(filename: str, content: bytes, user_id: str) -> list[dict]:
    user = get_or_create_user(user_id)
    content_hash = hashlib.sha256(content).hexdigest()

    cached = _find_existing(content_hash, user.id)
    if cached:
        return cached

    sheets = load_tabular(filename, content)
    if not sheets:
        raise ValueError(f"No readable data found in {filename}")

    settings = get_settings()
    s3 = get_s3_client()
    raw_key = f"raw/{content_hash}/{filename}"
    s3.put_object(Bucket=settings.s3_bucket, Key=raw_key, Body=content)

    multi_sheet = len(sheets) > 1
    created_ids = [
        _ingest_sheet(
            s3=s3,
            settings=settings,
            filename=filename,
            sheet_name=sheet_name,
            df=df,
            content_hash=content_hash,
            raw_key=raw_key,
            mime_type=_mime_type(filename),
            multi_sheet=multi_sheet,
            uploaded_by_user_id=user.id,
        )
        for sheet_name, df in sheets.items()
    ]

    return _load_summaries(created_ids)


def _find_existing(content_hash: str, uploaded_by_user_id: uuid.UUID) -> list[dict] | None:
    with get_session() as session:
        existing = session.scalars(
            select(Dataset)
            .where(
                Dataset.source_file_hash == content_hash,
                Dataset.uploaded_by_user_id == uploaded_by_user_id,
            )
            .options(selectinload(Dataset.versions).selectinload(DatasetVersion.columns))
        ).all()
        return [dataset_summary(d) for d in existing] if existing else None


def _load_summaries(dataset_ids: list[uuid.UUID]) -> list[dict]:
    with get_session() as session:
        datasets = session.scalars(
            select(Dataset)
            .where(Dataset.id.in_(dataset_ids))
            .options(selectinload(Dataset.versions).selectinload(DatasetVersion.columns))
        ).all()
        return [dataset_summary(d) for d in datasets]


def _ingest_sheet(
    *,
    s3,
    settings,
    filename: str,
    sheet_name: str,
    df,
    content_hash: str,
    raw_key: str,
    mime_type: str,
    multi_sheet: bool,
    uploaded_by_user_id: uuid.UUID,
) -> uuid.UUID:
    df = df.copy()
    df.columns = _clean_column_names(list(df.columns))

    profiles = profile_dataframe(df)
    for profile in profiles:
        is_pii, entities = scan_column(
            profile["name"], profile["sample_values"], is_categorical=profile["inferred_type"] == "categorical"
        )
        profile["is_pii"] = is_pii
        profile["pii_entities"] = entities

    reviewed = {}
    if review_columns is not None:
        try:
            reviewed = review_columns(profiles)
        except Exception:
            reviewed = {}

    for profile in profiles:
        review = reviewed.get(profile["name"], {})
        llm_type = review.get("inferred_type")
        if llm_type in ALLOWED_TYPES and is_plausible_override(llm_type, profile["distinct_count"]):
            profile["inferred_type"] = llm_type
        profile["description"] = review.get("description")

    df = coerce_types(df, profiles)

    dataset_id = uuid.uuid4()
    version_id = uuid.uuid4()
    parquet_key = f"datasets/{dataset_id}/v1/data.parquet"

    buffer = io.BytesIO()
    df.to_parquet(buffer, engine="pyarrow", index=False)
    s3.put_object(Bucket=settings.s3_bucket, Key=parquet_key, Body=buffer.getvalue())

    name = f"{_stem(filename)} - {sheet_name}" if multi_sheet else _stem(filename)

    with get_session() as session:
        dataset = Dataset(
            id=dataset_id,
            name=name,
            original_filename=filename,
            mime_type=mime_type,
            source_file_hash=content_hash,
            uploaded_by_user_id=uploaded_by_user_id,
        )
        session.add(dataset)
        session.flush()

        version = DatasetVersion(
            id=version_id,
            dataset_id=dataset_id,
            version_number=1,
            raw_key=raw_key,
            parquet_key=parquet_key,
            row_count=len(df),
            column_count=len(df.columns),
            description="original upload",
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

    return dataset_id


def _clean_column_names(names: list[str]) -> list[str]:
    cleaned = [str(n).strip().replace("\n", " ") for n in names]
    cleaned = [name if name else f"column_{i}" for i, name in enumerate(cleaned)]
    return _dedupe(cleaned)


def _dedupe(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    result = []
    for name in names:
        if name in seen:
            seen[name] += 1
            result.append(f"{name}_{seen[name]}")
        else:
            seen[name] = 0
            result.append(name)
    return result


def _mime_type(filename: str) -> str:
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    return {
        "csv": "text/csv",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }.get(ext, "application/octet-stream")


def _stem(filename: str) -> str:
    return filename.rsplit(".", 1)[0] if "." in filename else filename


def dataset_summary(dataset: Dataset) -> dict:
    active_version = next((v for v in dataset.versions if v.id == dataset.active_version_id), None)
    return {
        "id": str(dataset.id),
        "name": dataset.name,
        "original_filename": dataset.original_filename,
        "uploaded_by_user_id": str(dataset.uploaded_by_user_id) if dataset.uploaded_by_user_id else None,
        "created_at": dataset.created_at.isoformat() if dataset.created_at else None,
        "row_count": active_version.row_count if active_version else None,
        "column_count": active_version.column_count if active_version else None,
        "columns": (
            [
                {
                    "name": c.name,
                    "pandas_dtype": c.pandas_dtype,
                    "inferred_type": c.inferred_type,
                    "null_pct": c.null_pct,
                    "distinct_count": c.distinct_count,
                    "cardinality_ratio": c.cardinality_ratio,
                    "sample_values": c.sample_values,
                    "is_pii": c.is_pii,
                    "pii_entities": c.pii_entities,
                    "description": c.description,
                }
                for c in sorted(active_version.columns, key=lambda c: c.position)
            ]
            if active_version
            else []
        ),
    }
