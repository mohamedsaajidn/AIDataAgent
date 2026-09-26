import uuid

from sqlalchemy import select

from core.db import get_session
from core.models import User


def get_or_create_user(external_id: str, display_name: str | None = None) -> User:
    with get_session() as session:
        user = session.scalar(select(User).where(User.external_id == external_id))
        if user is None:
            user = User(id=uuid.uuid4(), external_id=external_id, display_name=display_name)
            session.add(user)
            session.flush()
        return user


class UserNotFound(Exception):
    pass


class InsufficientRole(Exception):
    pass


def require_editor(external_id: str) -> None:
    """Phase 7 RBAC gate for destructive actions (transform confirm, active-
    version repoint). Deliberately does NOT auto-create a missing user the
    way get_or_create_user does - a permission check has no business
    provisioning an identity, and an unknown external_id should fail the
    same way an under-privileged one does, not silently succeed.
    """
    with get_session() as session:
        user = session.scalar(select(User).where(User.external_id == external_id))
        if user is None:
            raise UserNotFound(f"No user found with external_id {external_id}")
        if user.role != "editor":
            raise InsufficientRole(f"User {external_id} has role '{user.role}', this action requires 'editor'")
