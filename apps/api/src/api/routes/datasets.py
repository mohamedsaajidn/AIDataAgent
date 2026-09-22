import uuid

from fastapi import APIRouter, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from api.services.ingestion import dataset_summary, ingest_file
from api.services.users import get_or_create_user
from core.db import get_session
from core.models import Dataset, DatasetVersion

router = APIRouter(prefix="/datasets", tags=["datasets"])


@router.post("/upload")
async def upload_dataset(file: UploadFile, user_id: str = Form(...)) -> dict:
    content = await file.read()
    if not content:
        raise HTTPException(400, "Uploaded file is empty")
    try:
        datasets = ingest_file(file.filename, content, user_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"datasets": datasets}


@router.get("")
def list_datasets(user_id: str) -> dict:
    # Scoped to the requesting user's own uploads - datasets from before
    # ownership existed (uploaded_by_user_id IS NULL) are unlisted here by
    # design, not shown to everyone as a "shared" fallback. See "Dataset
    # visibility" in CLAUDE.md for what this does and doesn't enforce.
    user = get_or_create_user(user_id)
    with get_session() as session:
        datasets = session.scalars(
            select(Dataset)
            .where(Dataset.uploaded_by_user_id == user.id)
            .options(selectinload(Dataset.versions).selectinload(DatasetVersion.columns))
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
