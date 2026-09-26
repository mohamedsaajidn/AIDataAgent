from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from api.services.transforms import (
    PendingTransformNotFound,
    PendingTransformNotPending,
    VersionNotFound,
    confirm_transform,
    export_version,
    list_versions,
    reject_transform,
    set_active_version,
)
from api.services.users import InsufficientRole, UserNotFound, require_editor

router = APIRouter(tags=["transforms"])


def _require_editor_or_403(user_id: str) -> None:
    try:
        require_editor(user_id)
    except (InsufficientRole, UserNotFound) as exc:
        raise HTTPException(403, "This action requires the 'editor' role.") from exc


@router.post("/transforms/{pending_id}/confirm")
def confirm(pending_id: str, user_id: str) -> dict:
    _require_editor_or_403(user_id)
    try:
        return confirm_transform(pending_id)
    except PendingTransformNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except PendingTransformNotPending as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/transforms/{pending_id}/reject")
def reject(pending_id: str) -> dict:
    try:
        return reject_transform(pending_id)
    except PendingTransformNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except PendingTransformNotPending as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/datasets/{dataset_id}/versions")
def get_versions(dataset_id: str) -> dict:
    try:
        return list_versions(dataset_id)
    except VersionNotFound as exc:
        raise HTTPException(404, str(exc)) from exc


class SetActiveVersionRequest(BaseModel):
    version_id: str


@router.post("/datasets/{dataset_id}/active-version")
def set_active(dataset_id: str, request: SetActiveVersionRequest, user_id: str) -> dict:
    _require_editor_or_403(user_id)
    try:
        return set_active_version(dataset_id, request.version_id)
    except VersionNotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/datasets/{dataset_id}/export")
def export(dataset_id: str, format: str = "csv", version_id: str | None = None) -> Response:
    try:
        content, filename, mime_type = export_version(dataset_id, version_id, format)
    except VersionNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return Response(
        content=content,
        media_type=mime_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
