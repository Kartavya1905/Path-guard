"""Seed local demo data. Idempotent. Does nothing when demo identity is off.

Run: python -m app.seed
"""

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_sessionmaker
from app.models import User

DEMO_USERS = [
    ("usr_demo0001", "Demo Planner"),
    ("usr_demo0002", "Demo Reviewer"),
    ("usr_demo0003", "Demo Outsider"),
]


def seed_demo_users(session: Session) -> None:
    for user_id, name in DEMO_USERS:
        if session.get(User, user_id) is None:
            session.add(User(id=user_id, display_name=name, is_demo=True))
    session.commit()


def main() -> None:
    if not get_settings().demo_identity_enabled:
        print("Demo identity disabled; nothing seeded.")
        return
    with get_sessionmaker()() as session:
        seed_demo_users(session)
    print(f"Seeded {len(DEMO_USERS)} demo users.")


if __name__ == "__main__":
    main()
