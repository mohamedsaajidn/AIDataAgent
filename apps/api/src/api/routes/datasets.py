import uuid

from fastapi import APIRouter, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from api.services.ingestion import dataset_summary, ingest_file
from core.db import get_session
from core.models import Dataset, DatasetVersion

router = APIRouter(prefix="/datasets", tags=["datasets"])


@router.post("/upload")
async def upload_dataset(file: UploadFile) -> dict:
    content = await file.read()
    if not content:
        raise HTTPException(400, "Uploaded file is empty")
    try:
        datasets = ingest_file(file.filename, content)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"datasets": datasets}


@router.get("")
def list_datasets() -> dict:
    with get_session() as session:
        datasets = session.scalars(
            select(Dataset).options(selectinload(Dataset.versions).selectinload(DatasetVersion.columns))
        ).all()
        return {"datasets": [dataset_summary(d) for d in datasets]}


@router.get("/{dataset_id}")
def get_dataset(dataset_id: str) -> dict:
    try:
        parsed_id = uuid.UUID(dataset_id)
    except ValueError as exc:
        raise HTTPException(400, "Invalid dataset id") from exc

    with get_session() as session:
        dataset = session.get(
            Dataset,
            parsed_id,
            options=[selectinload(Dataset.versions).selectinload(DatasetVersion.columns)],
        )
        if dataset is None:
            raise HTTPException(404, "Dataset not found")
        return dataset_summary(dataset)
