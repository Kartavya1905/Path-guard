"""Identity and project access.

Three modes (see app/config.py):
- demo: local only; seeded demo users, picked by header in tests.
- visitor: public demo hosting; each browser gets its own anonymous account in a
  signed, HttpOnly cookie, and only sees projects it created.
- none: every request is rejected with 401 until real authentication is added.

Local demo identity: when PATHGUARD_DEMO_IDENTITY_ENABLED is true (never in
production; config refuses it), every request acts as the seeded demo planner.
Tests and the local demo may pick another *seeded demo* user with the
X-PathGuard-Demo-User header. Real users can never be selected this way.

With demo identity off and no production authentication configured, every
request is rejected with 401. See docs/security.md.
"""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import delete, exists, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_session
from app.models import Member, ProjectRow, User, VersionRow

DEMO_USER_ID = "usr_demo0001"
DEMO_HEADER = "X-PathGuard-Demo-User"
VISITOR_COOKIE = "pathguard_visitor"

PERMISSIONS: dict[str, frozenset[str]] = {
    "owner": frozenset({"read", "write", "publish", "review", "admin"}),
    "editor": frozenset({"read", "write", "publish"}),
    "reviewer": frozenset({"read", "review"}),
    "viewer": frozenset({"read"}),
}

SessionDep = Annotated[Session, Depends(get_session)]


def current_user(request: Request, response: Response, session: SessionDep) -> User:
    settings = get_settings()
    if settings.demo_identity_enabled:
        user = session.get(User, request.headers.get(DEMO_HEADER, DEMO_USER_ID))
        if user is None or not user.is_demo:
            raise HTTPException(401, "Unknown demo user.")
        return user
    if settings.visitor_identity_enabled:
        return _visitor(request, response, session, settings)
    raise HTTPException(401, "Authentication is not configured for this deployment.")


# --------------------------------------------------------------------------- visitors


def _sign(user_id: str, key: str) -> str:
    return hmac.new(key.encode(), user_id.encode(), hashlib.sha256).hexdigest()


def visitor_cookie(user_id: str, key: str) -> str:
    return f"{user_id}.{_sign(user_id, key)}"


def _cookie_user_id(value: str | None, key: str) -> str | None:
    """The visitor ID in a cookie, or None if missing or tampered with."""
    if not value or "." not in value:
        return None
    user_id, sig = value.rsplit(".", 1)
    return user_id if hmac.compare_digest(sig, _sign(user_id, key)) else None


def _visitor(request: Request, response: Response, session: Session,
             settings: Settings) -> User:
    """Anonymous per-browser identity for the public demo.

    The cookie only proves "the same browser as before"; it is not a login. Each visitor
    can only reach projects they created, through the normal membership checks.
    """
    user_id = _cookie_user_id(request.cookies.get(VISITOR_COOKIE), settings.secret_key)
    user = session.get(User, user_id) if user_id else None
    if user is None:
        user = User(id=user_id or f"usr_{secrets.token_hex(10)}", display_name="Visitor",
                    is_demo=True)
        session.add(user)
        session.commit()  # read-only routes never commit, so persist the visitor now
    response.set_cookie(
        VISITOR_COOKIE, visitor_cookie(user.id, settings.secret_key),
        max_age=settings.visitor_retention_days * 86400, httponly=True, samesite="lax",
        secure=settings.env == "production", path="/")
    return user


def purge_expired_visitors(session: Session, settings: Settings) -> tuple[int, int]:
    """Delete visitor projects untouched for the retention period, then idle visitors.

    Returns (projects deleted, visitors deleted). Audit events are append-only and stay.
    """
    now = datetime.now(UTC)
    projects = session.execute(
        delete(ProjectRow)
        .where(ProjectRow.updated_at < now - timedelta(days=settings.visitor_retention_days))
        .returning(ProjectRow.id)).all()
    idle = (
        select(User.id)
        .where(User.created_at < now - timedelta(days=1), User.id.not_like("usr_demo%"))
        .where(~exists().where(ProjectRow.owner_id == User.id))
        .where(~exists().where(Member.user_id == User.id))
        .where(~exists().where(VersionRow.created_by == User.id))
    )
    users = session.execute(delete(User).where(User.id.in_(idle)).returning(User.id)).all()
    session.commit()
    return len(projects), len(users)


UserDep = Annotated[User, Depends(current_user)]


def project_access(
    session: Session, user: User, project_id: str, permission: str, *, lock: bool = False
) -> tuple[ProjectRow, str]:
    """Return (project, role) or raise 404 (not a member) / 403 (role too weak).

    Non-members get 404 so project IDs do not leak.
    """
    member = session.get(Member, (project_id, user.id))
    row = session.get(ProjectRow, project_id, with_for_update=lock) if member else None
    if member is None or row is None:
        raise HTTPException(404, "Project not found.")
    if permission not in PERMISSIONS[member.role]:
        raise HTTPException(403, f"Your role ({member.role}) cannot {permission} this project.")
    return row, member.role
