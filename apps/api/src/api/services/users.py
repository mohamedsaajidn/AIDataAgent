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
