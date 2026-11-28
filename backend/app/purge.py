"""Delete expired public-demo data. Does nothing unless visitor identity is on.

Run: python -m app.purge (for a visitor-mode deployment; run it on start).
"""

from app.auth import purge_expired_visitors
from app.config import get_settings
from app.db import get_sessionmaker


def main() -> None:
    settings = get_settings()
    if not settings.visitor_identity_enabled:
        print("Visitor identity disabled; nothing to purge.")
        return
    with get_sessionmaker()() as session:
        projects, users = purge_expired_visitors(session, settings)
    print(f"Purged {projects} expired projects and {users} idle visitors.")


if __name__ == "__main__":
    main()
