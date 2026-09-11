"""Dependencies shared by the extracted route groups (see ``routes/__init__``):
the per-app service context, the scope-path resolvers, and the live-worker
catalog reader. Everything here is module-level so a router can ``Depends`` on
it at import time; what a helper used to capture from the ``create_app``
closure (the queue) is an explicit parameter instead.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request

from ada.config import logger

from .. import auth as auth_module
from .. import db as db_module
from .. import failure_capture
from ..auth import User
from ..config import Settings
from ..queue import JobQueue
from ..scope import Scope
from ..scope import can_access as scope_can_access
from ..storage import Storage


@dataclass(frozen=True)
class RestContext:
    """The per-app services ``create_app`` builds once and stores on
    ``app.state.rest``; extracted routers read them through
    :func:`rest_context` instead of the closure."""

    settings: Settings
    storage: Storage
    queue: JobQueue

    async def audit(
        self,
        request: Request | None,
        user: User,
        scope: Scope,
        action: str,
        *,
        key: str | None = None,
        target_format: str | None = None,
        status: str | None = None,
        error: str | None = None,
        duration_ms: int | None = None,
        job_id: str | None = None,
        audit_run_id: str | None = None,
        pool=None,
    ) -> None:
        """:func:`audit_event` against this app's storage (the closure's old ``_audit``)."""
        await audit_event(
            self.storage,
            request,
            user,
            scope,
            action,
            key=key,
            target_format=target_format,
            status=status,
            error=error,
            duration_ms=duration_ms,
            job_id=job_id,
            audit_run_id=audit_run_id,
            pool=pool,
        )


def rest_context(request: Request) -> RestContext:
    """FastAPI dependency: the app's :class:`RestContext`."""
    return request.app.state.rest


# Hard cap on the regular API-buffered upload path. Above this we make
# the client request a presigned URL and PUT directly at the object
# store, so the API process never sees the bytes. 200 MB is high enough
# for typical IFC/Genie XML/STEP work and low enough that buffering it
# in Python doesn't blow the worker's RAM budget.
DIRECT_UPLOAD_THRESHOLD_BYTES: int = 200 * 1024 * 1024


async def audit_event(
    storage: Storage,
    request: Request | None,
    user: User,
    scope: Scope,
    action: str,
    *,
    key: str | None = None,
    target_format: str | None = None,
    status: str | None = None,
    error: str | None = None,
    duration_ms: int | None = None,
    job_id: str | None = None,
    audit_run_id: str | None = None,
    pool=None,
) -> None:
    """Best-effort audit row insert. No-ops without DB; never raises.

    Audit failures must not break user requests — a missing log line
    is preferable to a 500 on a successful upload.

    ``audit_run_id`` links the row to an admin-triggered audit
    sweep so the dispatcher can show per-cell pass/fail in the
    admin panel. NULL on every user-driven action.

    ``request`` is the FastAPI request when called from a route
    handler; the dispatcher / scheduler tick / issue-bot pass
    ``None`` instead and provide ``pool`` directly, since they
    run outside a request lifecycle. Either path is acceptable —
    we pick whichever pool source is available.
    """
    if pool is None and request is not None:
        pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        return
    # A failed row is only reproducible while its source still exists, and a
    # user-scope source can be deleted at any time — so preserve it now, not
    # when someone eventually opens the row. Content-addressed and
    # deduplicated, so a systematic failure copies each distinct input once;
    # returns None (and never raises) when disabled or ineligible.
    failure_key = None
    if failure_capture.is_failure(status):
        failure_key = await failure_capture.capture(storage, pool, db_module, scope=scope, key=key, action=action)
    try:
        await db_module.insert_audit(
            pool,
            user_sub=user.sub,
            scope_kind=scope.kind,
            scope_id=scope.id,
            action=action,
            key=key,
            target_format=target_format,
            status=status,
            error=error,
            duration_ms=duration_ms,
            job_id=job_id,
            audit_run_id=audit_run_id,
            failure_key=failure_key,
        )
    except Exception:
        logger.exception("audit insert failed (action=%s)", action)


def require_catalog_pool(request: Request):
    """The DB pool, or 503 — the equipment/system/engine catalogs need Postgres."""
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="catalogs disabled (no database configured)")
    return pool


# ── Scope helpers ────────────────────────────────────────────────
#
# Scope wire format: a single path segment / header value, one of
#   shared          — the shared bucket (any auth user)
#   user:me         — the caller's personal scope (resolved server-
#                     side to user.sub so URLs are user-agnostic)
#   project:<id>    — a project the caller is a member of
#
# Membership and project existence are checked against the DB; with
# no DB, project scopes are categorically inaccessible.


def parse_scope(s: str, user: User) -> Scope:
    if s == "shared":
        return Scope.shared()
    if s == "user:me":
        return Scope.user(user.sub)
    if s.startswith("user:"):
        # Naming another user explicitly is intentionally not
        # allowed; admins use phase-3 admin endpoints instead.
        raise HTTPException(
            status_code=400,
            detail="use 'user:me' for personal scope",
        )
    if s.startswith("project:"):
        pid = s[len("project:") :].strip()
        if not pid:
            raise HTTPException(status_code=400, detail="missing project id")
        return Scope.project(pid)
    if s.startswith("corpus:"):
        slug = s[len("corpus:") :].strip()
        if not slug:
            raise HTTPException(status_code=400, detail="missing corpus slug")
        # Admin-only gate fires in scope_can_access; here we just
        # parse. Non-admin requests hit a 403 at the access check.
        return Scope.corpus(slug)
    raise HTTPException(status_code=400, detail=f"invalid scope {s!r}")


async def resolve_project_scope(pool, scope: Scope) -> Scope:
    """Resolve ``project:<slug>`` to ``project:<uuid>`` against the DB.

    ``parse_scope`` doesn't know whether the id segment is a UUID or
    a slug — it just hands the raw string through. UUID-shaped ids
    pass through unchanged; non-UUID strings get looked up against
    ``projects.slug`` so callers can use the friendlier form in
    URLs and config files. Without a DB, slug lookup is impossible
    and ``can_access`` will reject regardless, so we leave the scope
    as-is.
    """
    if scope.kind != "project" or scope.id is None or pool is None:
        return scope
    import uuid as _uuid

    try:
        _uuid.UUID(scope.id)
        return scope
    except (ValueError, AttributeError, TypeError):
        pass
    resolved = await db_module.project_id_from_slug(pool, scope.id)
    if resolved is None:
        # Don't leak existence: same status as the membership check
        # below would have produced for a non-member of an unknown
        # project.
        raise HTTPException(status_code=403, detail="forbidden")
    return Scope.project(resolved)


async def scope_from_path(
    scope: str,
    request: Request,
    user: User = Depends(auth_module.current_user),
) -> Scope:
    s = parse_scope(scope, user)
    pool = getattr(request.app.state, "db_pool", None)
    s = await resolve_project_scope(pool, s)
    if not await scope_can_access(user, s, pool):
        raise HTTPException(status_code=403, detail="forbidden")
    return s


async def scope_from_header(
    request: Request,
    user: User = Depends(auth_module.current_user),
) -> Scope:
    s = parse_scope(request.headers.get("X-Scope", "shared"), user)
    pool = getattr(request.app.state, "db_pool", None)
    s = await resolve_project_scope(pool, s)
    if not await scope_can_access(user, s, pool):
        raise HTTPException(status_code=403, detail="forbidden")
    return s


def _merge_spec(base: dict, other: dict) -> None:
    """Fold a second worker's advertisement of the same slug into ``base``.

    Only the keys named in ``union_fields`` are combined, and only where both
    sides hold lists; everything else keeps the value the first worker supplied
    (see :func:`_live_worker_specs` for why "first" is well-defined). Order is
    preserved and duplicates dropped, so the result reads like one list somebody
    wrote rather than a concatenation.

    ``union_fields`` is itself unioned. That is what makes a rolling upgrade
    work: while half the pool runs a build that declares the key and half does
    not, the half that does still gets its fields merged instead of the
    behaviour flipping on whichever worker sorted first.
    """

    def _union(dst: list, src: list) -> list:
        seen = {json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v for v in dst}
        for v in src:
            marker = json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v
            if marker not in seen:
                seen.add(marker)
                dst.append(v)
        return dst

    fields = base.get("union_fields")
    fields = list(fields) if isinstance(fields, list) else []
    incoming = other.get("union_fields")
    if isinstance(incoming, list):
        fields = _union(fields, [f for f in incoming if isinstance(f, str)])
        base["union_fields"] = fields

    for key in fields:
        if not isinstance(key, str) or key == "union_fields":
            continue
        add = other.get(key)
        if not isinstance(add, list):
            continue
        have = base.get(key)
        if not isinstance(have, list):
            # The first worker did not carry the key at all (older build, or it
            # genuinely has nothing to contribute). Start from what this one
            # has rather than dropping it.
            base[key] = list(add)
        else:
            _union(have, add)


async def live_worker_specs(queue: JobQueue, field: str, fallback_field: str | None = None) -> dict[str, dict]:
    """Catalog-shaped specs advertised by non-stale workers of ``queue``, keyed by slug.
    Falls back to a bare slug-list field for older workers that advertise
    only names, synthesizing a minimal spec.

    WHEN SEVERAL WORKERS ADVERTISE ONE SLUG, two rules apply:

    * **Deterministic, not last-writer-wins.** Workers are visited in
      worker-id order and the first spec for a slug supplies the scalars.
      Previously this followed KV listing order, so with two workers on one
      plugin the advertised version and capability could differ between two
      consecutive requests for no visible reason.
    * **Declared list fields are unioned.** A spec may name keys in
      ``union_fields``; those are combined across every worker advertising
      the slug instead of one worker's copy winning. That is what lets a
      sharded pool say what it collectively covers — several workers on the
      same plugin, each serving a different project, produce one spec
      listing every project that is online.

    Only the named keys are unioned. A blanket "merge every list" would
    quietly combine things that are per-worker facts rather than collective
    ones (a worker's own conversions, its own extension allowlist), and
    produce a spec describing a worker that does not exist.
    """
    import time as _time

    out: dict[str, dict] = {}
    if not queue.enabled:
        return out
    now = _time.time()
    # Sorted so the winner is stable across requests. `worker_id` is always
    # present — list_workers derives it from the KV key.
    workers = sorted(await queue.list_workers(), key=lambda w: str(w.get("worker_id") or ""))
    for w in workers:
        hb = w.get("last_heartbeat")
        if not (isinstance(hb, (int, float)) and (now - hb) <= queue.WORKER_STALE_AFTER_S):
            continue
        # What this worker declined to serve, and why. A spec whose
        # capability is withheld is carried through as UNAVAILABLE rather
        # than dropped: a withheld capability and an absent one look
        # identical to every consumer otherwise, and the consumer then
        # tells the operator to start a worker that is already running.
        # See deploy/worker-trust.md §4.
        withheld = {
            str(x.get("capability", "")).strip().lower(): str(x.get("reason") or "")
            for x in (w.get("withheld") or [])
            if isinstance(x, dict) and x.get("capability")
        }
        specs = w.get(field)
        if isinstance(specs, list):
            for s in specs:
                if isinstance(s, dict) and isinstance(s.get("slug"), str) and s["slug"]:
                    slug = s["slug"]
                    cap = str(s.get("worker_capability") or "").strip().lower()
                    reason = withheld.get(cap) if cap else None
                    entry = dict(s)
                    if reason:
                        entry["available"] = False
                        entry["unavailable_reason"] = reason
                    if slug not in out:
                        out[slug] = entry
                    elif reason is None and out[slug].get("available") is False:
                        # A FIT worker overrides an unfit one's verdict: the
                        # capability is available from somewhere, which is
                        # what the caller actually needs to know. Order of
                        # workers must not decide this.
                        merged = dict(out[slug])
                        merged.pop("available", None)
                        merged.pop("unavailable_reason", None)
                        out[slug] = merged
                        _merge_spec(out[slug], entry)
                    else:
                        _merge_spec(out[slug], entry)
        elif fallback_field:
            bare = w.get(fallback_field)
            if isinstance(bare, list):
                for slug in bare:
                    if isinstance(slug, str) and slug and slug not in out:
                        out[slug] = {"slug": slug, "name": slug.replace("_", " ").title()}
    return out


async def advertised_engine_capability(queue: JobQueue, slug: str | None) -> str | None:
    """The worker capability of an engine a live worker advertises itself.

    Complements the database lookup at every routing site: a self-advertising
    engine has no row, so without this its jobs would silently route to the
    DEFAULT pool -- the engine would appear in the list, be selectable, and
    then run somewhere that does not have it.

    A row always wins where one exists; this is only consulted when the
    lookup came back empty.
    """
    if not slug:
        return None
    from ada.comms.engine_specs import is_offerable

    spec = (await live_worker_specs(queue, "procedural_engine_specs")).get(slug)
    if spec is None or not is_offerable(spec):
        return None
    return spec.get("worker_capability")
