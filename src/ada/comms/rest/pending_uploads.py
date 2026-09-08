"""In-process bookkeeping for a presigned upload between mint and completion.

``POST /upload-url`` hands the browser a URL and forgets: the object store has
no notion of "this key is mid-upload" (that is what makes it fast — no request
body passes through this process at all), and until now nothing else in the API
recorded one either. A key can therefore sit in a state where a listing, a
convert, or a bake job sees it before the upload it belongs to has actually
finished — whether that is because the PUT is still in flight and the backing
store does not hide a partial object from a concurrent GET, or simply because
the browser never called ``/upload-complete`` (tab closed, network dropped).
Either way, dispatching work against that key finds bytes that are not what the
caller thinks they are, and fails wherever the reader first needs the part
that is missing — reported in the wild as
``TypeError: 'NoneType' object is not subscriptable`` several frames into a
Sesam SIN parse, which is a true statement about the code and no help at all to
whoever is looking at it.

This module is the guard rail. ``upload-url`` registers a pending upload here;
``upload-complete`` clears it. Anything that would dispatch work against a
key — ``/convert``, ``/fea/manifest``, ``/utility``, a plugin job — checks it
first and answers 409 with the same information a progress bar would want
(bytes so far, if the browser has said) rather than letting the job run into a
reader that cannot make sense of what it finds. ``GET /scopes/{scope}/files``
merges pending entries into its listing the same way, so a second tab, a second
user, or the same tab after a reload sees "still uploading" instead of a file
that looks ready and is not.

DELIBERATELY IN-PROCESS, NOT PERSISTED. This mirrors ``local_jobs.py``'s own
reasoning for the same trade-off: it covers the default single-replica API
deployment (``deploy/helm/adapy-viewer/values.yaml``: ``replicaCount: 1``) with
nothing to provision — no bucket, no table, no dependency on Postgres or NATS
being configured. Running multiple API replicas would give each its own view:
a request that happens to land on a replica which never saw the ``upload-url``
call would not be gated when it should have been. It would never be gated
*wrongly* — a false negative here is a bug that was already there; a false
positive (blocking a finished upload) is not possible, since nothing here
survives past the TTL or a matching ``mark_complete``. If this stack grows a
second replica in front of a real workload, move the registry into the audit
table (already optional) instead of reaching for a lock service to protect a
plain dict.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .scope import Scope

#: Matches the default TTL ``presigned_put_url`` mints with — a pending entry
#: outliving the URL it tracks would just be a permanent false "uploading".
DEFAULT_TTL_SECONDS = 3600.0


@dataclass
class PendingUpload:
    scope_prefix: str
    key: str
    user_id: str
    started_at: float
    expires_at: float
    #: Client-declared size, if the caller sent one. Not verified against
    #: anything — it is a hint for a progress bar, never a correctness check.
    size_hint: int | None = None
    #: Last heartbeat from the browser's own upload-progress event, if any
    #: arrived. ``None`` until the first one does; a caller with nothing to
    #: report yet still gets "uploading", just with no percentage attached.
    loaded: int | None = None
    total: int | None = None
    updated_at: float | None = None

    def is_expired(self, *, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) >= self.expires_at

    def as_dict(self) -> dict:
        """Fields merged into a ``/files`` row. ``size`` is always present
        (falling back to 0) because ``FileEntry``/``AdminFileEntry`` on the
        frontend declare it required — every OTHER row in that listing is a
        real object with a real size, and a row that sometimes has the field
        and sometimes doesn't is a worse contract than one that is 0 while
        genuinely unknown."""
        out: dict = {
            "status": "uploading",
            "started_at": self.started_at,
            "size": self.size_hint if self.size_hint is not None else 0,
        }
        if self.loaded is not None and self.total is not None:
            out["upload_progress"] = {
                "loaded": self.loaded,
                "total": self.total,
                "updated_at": self.updated_at,
            }
        return out


# (scope_prefix, key) -> PendingUpload. Module-level and unlocked: every
# mutation here is a single dict assignment/pop, which the GIL already makes
# atomic, and nothing in this module ever awaits between reading a value and
# writing it back — there is no window for a lock to close.
_REGISTRY: dict[tuple[str, str], PendingUpload] = {}


def _norm_key(key: str) -> str:
    return key.strip().lstrip("/")


def _registry_key(scope: "Scope", key: str) -> tuple[str, str]:
    return (scope.prefix(), _norm_key(key))


def mark_pending(
    scope: "Scope",
    key: str,
    *,
    user_id: str,
    size_hint: int | None = None,
    ttl_seconds: float = DEFAULT_TTL_SECONDS,
) -> None:
    """Record that a presigned upload was minted for ``key`` and has not
    completed. Replaces any existing entry for the same key outright — a
    retried upload (the common reason a second ``upload-url`` call happens
    for a key already pending) restarts the window rather than stacking."""
    now = time.time()
    entry = PendingUpload(
        scope_prefix=scope.prefix(),
        key=_norm_key(key),
        user_id=user_id,
        started_at=now,
        expires_at=now + ttl_seconds,
        size_hint=size_hint,
    )
    _REGISTRY[_registry_key(scope, key)] = entry


def mark_complete(scope: "Scope", key: str) -> None:
    """Clear a pending upload — the browser's PUT succeeded and
    ``/upload-complete`` confirmed the object exists. A no-op if there was
    nothing pending (the regular small-file PUT path never registers one, and
    calling this twice must not raise)."""
    _REGISTRY.pop(_registry_key(scope, key), None)


def heartbeat(scope: "Scope", key: str, *, loaded: int, total: int) -> bool:
    """Record upload progress the browser reported. Returns whether a pending
    entry existed to update — the caller (a heartbeat endpoint) uses this to
    tell the browser to stop bothering rather than 404ing that harmless call,
    since "nothing pending" is the expected end state once the upload finishes
    and the last heartbeat races the ``upload-complete`` clear."""
    entry = get(scope, key)
    if entry is None:
        return False
    entry.loaded = max(0, loaded)
    entry.total = max(entry.loaded, total)
    entry.updated_at = time.time()
    return True


def get(scope: "Scope", key: str) -> PendingUpload | None:
    """The pending entry for ``key``, or ``None`` if there isn't one — reaping
    it first if its TTL has passed, so an abandoned upload does not block that
    key forever."""
    reg_key = _registry_key(scope, key)
    entry = _REGISTRY.get(reg_key)
    if entry is None:
        return None
    if entry.is_expired():
        _REGISTRY.pop(reg_key, None)
        return None
    return entry


def is_pending(scope: "Scope", key: str) -> bool:
    return get(scope, key) is not None


def list_for_scope(scope: "Scope") -> dict[str, PendingUpload]:
    """Every non-expired pending upload in ``scope``, keyed by (normalised) key.

    Used by ``GET /scopes/{scope}/files`` to merge "uploading" rows into a
    listing. Expired entries are reaped as they are found, same as ``get``.
    """
    prefix = scope.prefix()
    now = time.time()
    out: dict[str, PendingUpload] = {}
    for (scope_prefix, key), entry in list(_REGISTRY.items()):
        if scope_prefix != prefix:
            continue
        if entry.is_expired(now=now):
            _REGISTRY.pop((scope_prefix, key), None)
            continue
        out[key] = entry
    return out
