from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session, sessionmaker

from core.storage import get_engine


@contextmanager
def get_session() -> Iterator[Session]:
    session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
