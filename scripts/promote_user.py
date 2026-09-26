"""One-off role change for a user - RBAC roles are deliberately not settable
via any API request (see core.models.User's docstring for why), so this is
the only way to promote/demote a user outside a direct DB update.

Run: uv run python scripts/promote_user.py <external_id> <viewer|editor>
"""

import sys

from sqlalchemy import select

from core.db import get_session
from core.models import User


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[2] not in ("viewer", "editor"):
        print("Usage: uv run python scripts/promote_user.py <external_id> <viewer|editor>")
        raise SystemExit(1)

    external_id, role = sys.argv[1], sys.argv[2]
    with get_session() as session:
        user = session.scalar(select(User).where(User.external_id == external_id))
        if user is None:
            print(f"No user found with external_id={external_id!r}")
            raise SystemExit(1)
        previous = user.role
        user.role = role
        print(f"{external_id}: {previous} -> {role}")


if __name__ == "__main__":
    main()
