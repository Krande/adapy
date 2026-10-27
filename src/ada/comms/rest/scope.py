"""Storage scopes for the multi-tenant REST viewer.

Four tiers, each mapped to a stable prefix in the bucket:

* ``shared``                — visible to every authenticated user.
* ``projects/<project_id>`` — visible to members of the project (DB-managed).
* ``users/<user_sub>``      — visible only to the owning user.
* ``corpus/<slug>``         — admin-curated regression corpus. Admin-only
                              on every axis (list, read, write, audit).
* ``providers``             — the provider store: provider data kept once,
                              whatever scope a job ran from. Admin-only
                              directly; other scopes read it through grants.

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

ScopeKind = Literal["shared", "project", "user", "corpus", "providers"]

#: Kinds that name exactly one scope and carry no id.
_SINGLETON_KINDS = ("shared", "providers")


@dataclass(frozen=True)
class Scope:
    kind: ScopeKind
    # project_id (uuid string) for ``project``, user_sub for ``user``,
    # slug for ``corpus``, None for ``shared``. Stored as a plain
    # string so it round-trips cleanly through URL paths regardless of
    # source.
    id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.kind in _SINGLETON_KINDS:
            if self.id is not None:
                raise ValueError(f"{self.kind} scope has no id")
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
        if self.kind == "providers":
            return "providers"
        raise AssertionError(f"unknown scope kind {self.kind!r}")

    def wire(self) -> str:
        """This scope as the API PATH SEGMENT that parses back to it.

        The inverse of the REST layer's scope parser, and distinct from
        :meth:`prefix` -- which is the storage key. Both are strings that name a
        scope and they are NOT interchangeable: ``prefix()`` for a project is
        ``projects/<uuid>``, and a ``/`` in a path segment makes the URL match a
        different route (or none), which surfaces as a 405 rather than as anything
        that mentions scopes.

        This exists because that mistake was made: the source-node REST recorder
        built its URL from ``prefix()``, so every write it ever attempted hit a
        405 and was reported as "the API refused the request". A method whose name
        says which string it is, is the fix -- the previous state of affairs
        offered only ``prefix()``, which looks plausible in a URL.

        A ``user`` scope has no wire form a third party can use: the parser
        accepts only ``user:me``, deliberately, and returning that here would make
        a caller acting for someone else write into its OWN scope. Silently
        correct-looking data in the wrong place is worse than an error, so this
        refuses.
        """
        if self.kind in _SINGLETON_KINDS:
            return self.kind
        if self.kind in ("project", "corpus"):
            return f"{self.kind}:{self.id}"
        if self.kind == "user":
            raise ValueError(
                "a user scope has no wire form that names it to another caller: the API accepts "
                "only 'user:me', which would resolve to whoever is calling"
            )
        raise AssertionError(f"unknown scope kind {self.kind!r}")

    @classmethod
    def from_prefix(cls, prefix: str) -> "Scope":
        """The scope a stored prefix names -- the inverse of :meth:`prefix`, for the tables that
        record scopes that way (``attr_collections.scope``, ``asset_export_runs.scope``). Raises
        ValueError for anything :meth:`prefix` could not have produced. Whether the scope exists
        (a project row) is not checked here."""
        if prefix == "shared":
            return cls.shared()
        if prefix == "providers":
            return cls.providers()
        kind, sep, ident = prefix.partition("/")
        kinds = {"projects": "project", "users": "user", "corpus": "corpus"}
        if not sep or kind not in kinds or not ident or ident != ident.strip() or "/" in ident:
            raise ValueError(f"not a scope prefix: {prefix!r} (shared, projects/<id>, users/<sub> or corpus/<slug>)")
        return cls(kind=kinds[kind], id=ident)  # type: ignore[arg-type]

    @classmethod
    def shared(cls) -> "Scope":
        return cls(kind="shared")

    @classmethod
    def providers(cls) -> "Scope":
        """The provider store: the ONE place provider data (published assets, attribute index,
        change feed) lives, whichever scope a job was started from. Nobody reads it directly
        except an admin; a scope reads it through a grant (``db/provider_grants.py``)."""
        return cls(kind="providers")

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
    if scope.kind in ("corpus", "providers"):
        # The provider store is read DIRECTLY by admins only. Everyone else reads the datasets
        # granted to a scope they can open, through that scope (see db/provider_grants.py).
        return bool(getattr(user, "is_admin", False))
    return False
