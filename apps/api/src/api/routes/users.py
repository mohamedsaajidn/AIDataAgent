from fastapi import APIRouter

from api.services.users import get_or_create_user

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me")
def get_me(user_id: str) -> dict:
    """Read-only identity/role lookup for the frontend - lets the UI show a
    role badge and pre-disable actions a viewer can't perform, instead of
    only finding out via a 403 after clicking. Resolves-or-creates exactly
    like every other endpoint that takes user_id (get_or_create_user never
    sets role), so this never grants anything - a brand new external_id
    still comes back "viewer".
    """
    user = get_or_create_user(user_id)
    return {"external_id": user.external_id, "role": user.role}
