"""Storage scopes for the multi-tenant REST viewer.

Four tiers, each mapped to a stable prefix in the bucket:

* ``shared``                — visible to every authenticated user.
* ``projects/<project_id>`` — visible to members of the project (DB-managed).
* ``users/<user_sub>``      — visible only to the owning user.
* ``corpus/<slug>``         — admin-curated regression corpus. Admin-only
                              on every axis (list, read, write, audit).

Derived blobs inherit their source's scope: a user-scoped IFC produces
a user-scoped GLB at ``users/<sub>/_derived/foo.ifc.glb``. The Storage
layer takes a :class:`Scope` per call and builds the on-bucket prefix;
this module only owns the scope→prefix mapping and authorization
checks. URL parsing lives in :mod:`app.py`; DB queries live in
:mod:`db.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from .auth import User

ScopeKind = Literal["shared", "project", "user", "corpus"]


@dataclass(frozen=True)
class Scope:
    kind: ScopeKind
    # project_id (uuid string) for ``project``, user_sub for ``user``,
    # slug for ``corpus``, None for ``shared``. Stored as a plain
    # string so it round-trips cleanly through URL paths regardless of
    # source.
    id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.kind == "shared":
            if self.id is not None:
                raise ValueError("shared scope has no id")
        else:
            if not self.id:
                raise ValueError(f"{self.kind} scope requires an id")

    def prefix(self) -> str:
        """Bucket-relative prefix this scope occupies. No leading slash."""
        if self.kind == "shared":
            return "shared"
        if self.kind == "project":
            return f"projects/{self.id}"
        if self.kind == "user":
            return f"users/{self.id}"
        if self.kind == "corpus":
            return f"corpus/{self.id}"
        raise AssertionError(f"unknown scope kind {self.kind!r}")

    @classmethod
    def shared(cls) -> "Scope":
        return cls(kind="shared")

    @classmethod
    def project(cls, project_id: str) -> "Scope":
        return cls(kind="project", id=project_id)

    @classmethod
    def user(cls, user_sub: str) -> "Scope":
        return cls(kind="user", id=user_sub)

    @classmethod
    def corpus(cls, slug: str) -> "Scope":
        return cls(kind="corpus", id=slug)


async def can_access(user: User, scope: Scope, db_pool=None) -> bool:
    """Authorization check.

    * ``shared`` — any authenticated user.
    * ``user`` — only the owning user (sub matches scope.id).
    * ``project`` — members listed in ``project_members``. Requires a
      DB pool; without one (no-DB deployments) project scopes are
      categorically inaccessible.
    * ``corpus`` — admin only (regardless of which slug). Corpora are
      curated proprietary regression assets; non-admins shouldn't
      see they exist, let alone list / download files. The admin
      panel + audit dispatcher are the only legitimate readers.

    Admins are *not* automatically granted cross-tenant access for
    shared / user / project scopes — admin status is for the admin
    panel, not for casually browsing other users' files. Cross-tenant
    access is an explicit admin endpoint (phase 3) that audits.
    """
    if scope.kind == "shared":
        return True
    if scope.kind == "user":
        return scope.id == user.sub
    if scope.kind == "project":
        if db_pool is None:
            return False
        # Imported here to keep scope.py free of asyncpg in the hot path
        # — this lets non-DB deployments load scope.py without pulling
        # in the DB layer.
        from . import db as db_module

        return await db_module.is_project_member(db_pool, scope.id or "", user.sub)
    if scope.kind == "corpus":
        return bool(getattr(user, "is_admin", False))
    return False
