"""Stable identifiers.

Every entity ID is `<prefix>_<token>`. Random IDs are used for things people
create; deterministic IDs (a hash of their defining parts) are used for
rule-generated suggestions so regenerating a map yields the same IDs and
duplicates can be detected.
"""

import hashlib
import secrets
from typing import Annotated

from pydantic import StringConstraints

PREFIXES = {
    "project": "prj",
    "task": "tsk",
    "prerequisite": "pre",
    "node": "nod",
    "transition": "trn",
    "decision_group": "dgp",
    "risk": "rsk",
    "evidence": "evd",
    "evidence_link": "evl",
    "assumption": "asm",
    "mitigation": "mit",
    "note": "not",
    "version": "ver",
    "outcome": "out",
    "user": "usr",
}

ID_PATTERN = r"^[a-z]{3}_[A-Za-z0-9]{8,40}$"
EntityId = Annotated[str, StringConstraints(pattern=ID_PATTERN)]


def new_id(kind: str) -> str:
    """Random ID for a user-created entity."""
    return f"{PREFIXES[kind]}_{secrets.token_hex(8)}"


def stable_id(kind: str, *parts: str) -> str:
    """Deterministic ID: same kind and parts always give the same ID."""
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:20]
    return f"{PREFIXES[kind]}_{digest}"
