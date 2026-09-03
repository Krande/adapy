"""Preserve the input of a failed job in an admin-only failure corpus.

A failure is reproducible only while the file it failed on still exists. That
file belongs to the user, who may delete or replace it at any time, while the
``audit_log`` row survives — so ``GET /admin/audit/{id}/source`` 404s on exactly
the rows worth investigating. Copying at failure time closes that race.

The destination is the ``corpus`` scope: already admin-only on every axis and
already in the admin scope picker, so this adds no authorization surface.

Keys are content-addressed on the source's storage identity, so identical bytes
are stored once however many rows point at them — ``failure_key`` is a pointer,
and nothing here may delete a blob another row still references.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os

from .scope import Scope

logger = logging.getLogger(__name__)

ENABLED_ENV = "ADA_VIEWER_FAILURE_CORPUS"
SLUG_ENV = "ADA_VIEWER_FAILURE_CORPUS_SLUG"
TIMEOUT_ENV = "ADA_VIEWER_FAILURE_CORPUS_TIMEOUT_S"

_DEFAULT_SLUG = "failures"
_DEFAULT_TIMEOUT_S = 30.0

#: Terminal statuses that mean "this job failed on its input".
FAILED_STATUSES = frozenset({"error", "failed"})

#: Scopes whose files someone can delete. ``corpus`` is absent: those assets are
#: frozen, so a row naming one stays reproducible without a copy.
CAPTURED_SCOPE_KINDS = frozenset({"user", "shared", "project"})

#: Actions whose input IS a derived blob. A conversion reads a source and derived
#: output is rebuildable from it, so capturing that would be redundant — but a
#: render reads the derived artifact, and re-deriving it can produce different
#: bytes than the ones that actually failed. For these, the derived blob is the
#: evidence.
DERIVED_INPUT_ACTIONS = frozenset({"view", "render"})

_TRUE = {"1", "true", "yes", "on"}

# Slugs whose corpus row this process has ensured. Capture runs on every failure;
# re-checking a row that cannot vanish mid-process would cost a query each time.
_ensured_slugs: set[str] = set()


def enabled() -> bool:
    return os.environ.get(ENABLED_ENV, "").strip().lower() in _TRUE


def slug() -> str:
    return os.environ.get(SLUG_ENV, "").strip() or _DEFAULT_SLUG


def timeout_s() -> float:
    try:
        return float(os.environ.get(TIMEOUT_ENV, "").strip() or _DEFAULT_TIMEOUT_S)
    except ValueError:
        return _DEFAULT_TIMEOUT_S


def failure_scope() -> Scope:
    return Scope.corpus(slug())


def is_failure(status: str | None) -> bool:
    return (status or "").strip().lower() in FAILED_STATUSES


def _identity(head: dict, scope: Scope, key: str) -> str:
    """Token naming *these bytes*, from object metadata alone.

    ``e_tag`` is the content discriminator when the backend reports one (S3 and
    Garage do). It is an MD5 of the content for single-part uploads, so identical
    files collapse; for multipart it also encodes the part layout, so identical
    content uploaded with different part sizes stores twice. That costs a
    duplicate, never a wrong hit. Size is mixed in so a token can only be shared
    by objects agreeing on both.

    Without an e_tag there is no content signal short of downloading the blob, so
    the fallback keys on object identity: repeat failures of one object still
    collapse, two identical files do not.
    """
    etag = (head.get("e_tag") or "").strip().strip('"')
    size = head.get("size")
    if etag:
        material = f"etag:{etag}:{size}"
    else:
        material = f"obj:{scope.prefix()}/{key}:{size}:{head.get('last_modified')}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def destination_key(head: dict, scope: Scope, key: str) -> str:
    """``<identity><ext>``.

    The filename is excluded on purpose: including it would store the same bytes
    twice under two names. Nothing is lost, since the audit row keeps the key the
    failure happened on. The extension is kept because the repro path dispatches
    on it, and identical bytes offered as two formats are two reproductions.
    """
    basename = key.rsplit("/", 1)[-1]
    ext = ""
    if "." in basename:
        ext = "." + basename.rsplit(".", 1)[-1].lower()
    return f"{_identity(head, scope, key)}{ext}"


def should_capture(scope_kind: str | None, key: str | None, action: str | None = None) -> bool:
    from .converter import is_derived_key

    if not enabled():
        return False
    if scope_kind not in CAPTURED_SCOPE_KINDS or not key:
        return False
    if is_derived_key(key) and action not in DERIVED_INPUT_ACTIONS:
        return False
    return True


async def _ensure_corpus_row(pool, db_module) -> None:
    """Register the corpus so it appears in the admin scope picker.

    Storage needs no registration, but ``/api/me`` builds the picker from the
    ``corpora`` table — without a row the captured files are unreachable from
    the UI.
    """
    name = slug()
    if name in _ensured_slugs or pool is None:
        return
    try:
        if await db_module.get_corpus_by_slug(pool, name) is None:
            await db_module.create_corpus(
                pool,
                slug=name,
                name="Failure corpus",
                description="Inputs preserved automatically when a job failed on them.",
                created_by=None,
            )
    except Exception:
        # Losing the insert race is the expected case (partial-unique on slug),
        # and an unregistered corpus is no reason to drop the bytes.
        logger.debug("failure capture: could not ensure corpus row %r", name, exc_info=True)
    _ensured_slugs.add(name)


async def capture(storage, pool, db_module, *, scope: Scope, key: str, action: str | None = None) -> str | None:
    """Copy ``scope/key`` into the failure corpus; return the destination key.

    ``None`` when disabled, ineligible, already gone, or on any error. Never
    raises — losing the copy beats losing the audit row it belongs to.
    """
    if not should_capture(scope.kind, key, action):
        return None
    try:
        return await asyncio.wait_for(_capture(storage, pool, db_module, scope, key), timeout=timeout_s())
    except asyncio.TimeoutError:
        logger.warning("failure capture: timed out copying %s from %s", key, scope.prefix())
        return None
    except Exception:
        logger.exception("failure capture: could not preserve %s from %s", key, scope.prefix())
        return None


async def _capture(storage, pool, db_module, scope: Scope, key: str) -> str | None:
    head = await storage.head(scope, key)
    if head is None:
        logger.info("failure capture: %s in %s is already gone", key, scope.prefix())
        return None

    dst_scope = failure_scope()
    dst_key = destination_key(head, scope, key)
    if await storage.exists(dst_scope, dst_key):
        return dst_key

    await _ensure_corpus_row(pool, db_module)
    # overwrite=True because the safe default raises "copy-if-not-exists not
    # supported" on S3; exists() above is the collision guard, and a lost race
    # rewrites identical content under a content-addressed key.
    await storage.copy(scope, key, dst_scope, dst_key, overwrite=True)
    logger.info("failure capture: preserved %s as %s/%s", key, dst_scope.prefix(), dst_key)
    return dst_key


async def capture_for_job(storage, pool, db_module, job_id: str) -> str | None:
    """Capture for a queued job, resolving scope/key/action from its audit row.

    The worker's error paths carry only a job id, and there are a dozen of them;
    reading the row back keeps capture to one call site per funnel.
    """
    if not enabled() or pool is None:
        return None
    try:
        row = await db_module.get_audit_by_job(pool, job_id)
    except Exception:
        logger.exception("failure capture: could not resolve audit row for job %s", job_id)
        return None
    if row is None:
        return None
    kind, key, action = row.get("scope_kind"), row.get("key"), row.get("action")
    if not should_capture(kind, key, action):
        return None
    scope = Scope.shared() if kind == "shared" else Scope(kind=kind, id=row.get("scope_id"))
    return await capture(storage, pool, db_module, scope=scope, key=key, action=action)
