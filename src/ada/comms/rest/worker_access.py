"""Per-user and per-capability worker-pool authorization policy.

Pure evaluation helpers (:func:`is_worker_allowed`, :func:`policy_from_row`) live
alongside the async enforcement guard (:func:`require_worker_access`) invoked by
:meth:`ada.comms.rest.job_transport.QueueJobTransport.submit`.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass

from fastapi import HTTPException

from . import db as db_module
from .auth import User

# The policy governs the CHOICE of the worker pool over the browser, not server
# compute as such. A workload with no browser path (an Excel export, an asset
# build, a clash check, a detail-LOD compile, an .odb bake, a plugin without a
# baked wheel) has nothing to choose between and is never refused. A client that
# could run a job in the browser and sends it to the worker instead marks that
# request with this header; only those submissions are judged.
RUN_ON_HEADER = "x-ada-run-on"
RUN_ON_WORKER = "worker"

_explicit_worker_choice: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "ada_explicit_worker_choice", default=False
)


def set_explicit_worker_choice(value: bool) -> contextvars.Token:
    return _explicit_worker_choice.set(bool(value))


def reset_explicit_worker_choice(token: contextvars.Token) -> None:
    _explicit_worker_choice.reset(token)


def explicit_worker_choice() -> bool:
    """True when the current request chose the worker over an in-browser alternative."""
    return _explicit_worker_choice.get()


def requests_worker(headers) -> bool:
    """Whether request ``headers`` carry ``X-Ada-Run-On: worker``."""
    return str(headers.get(RUN_ON_HEADER, "")).strip().lower() == RUN_ON_WORKER


@dataclass(frozen=True)
class WorkerAccessPolicy:
    """Effective worker-pool policy for a user."""

    enabled: bool = False
    allowed_capabilities: tuple[str, ...] = ()
    updated_by: str | None = None
    updated_at: str | None = None


def default_policy() -> WorkerAccessPolicy:
    return WorkerAccessPolicy()


def policy_from_row(row: dict | None) -> WorkerAccessPolicy:
    if not row:
        return default_policy()
    raw_caps = row.get("allowed_capabilities") or ()
    caps = tuple(sorted({str(c).strip().lower() for c in raw_caps if str(c).strip()}))
    return WorkerAccessPolicy(
        enabled=bool(row.get("enabled", False)),
        allowed_capabilities=caps,
        updated_by=row.get("updated_by"),
        updated_at=row.get("updated_at"),
    )


def is_worker_allowed(user: User | None, policy: WorkerAccessPolicy, capability: str | None) -> bool:
    """Return True if ``user`` is permitted to enqueue a server worker job for ``capability``.

    - ``user is None`` (internal/scheduled background tasks) is always allowed.
    - ``user.is_admin`` is always allowed.
    - ``policy.enabled`` grants global worker-pool access across all capabilities.
    - Otherwise, ``capability`` must appear in ``policy.allowed_capabilities``.
    """
    if user is None or user.is_admin:
        return True
    if policy.enabled:
        return True
    if capability is None:
        return False
    return capability.strip().lower() in {c.lower() for c in policy.allowed_capabilities}


def serialize_me_worker_access(user: User, policy: WorkerAccessPolicy) -> dict:
    """Shape returned under ``GET /api/me`` -> ``worker_access``."""
    return {
        "enabled": bool(policy.enabled),
        "allowed_capabilities": list(policy.allowed_capabilities),
        "effective_all": bool(user.is_admin or policy.enabled),
    }


def worker_access_applies(pool, user: User | None, *, explicit: bool | None = None) -> bool:
    """True when a submission by ``user`` has to pass the policy check at all.

    False for system/scheduled ticks (no user), admins, a DB-less deployment
    (shared-only, no ``user_worker_access`` table and no admin panel to grant it),
    and any submission that did not choose the worker over the browser
    (``explicit``, default: the current request's ``X-Ada-Run-On`` header).
    """
    if explicit is None:
        explicit = explicit_worker_choice()
    return explicit and user is not None and not user.is_admin and pool is not None


async def require_worker_access(pool, user: User | None, capability: str | None) -> None:
    """Enforce worker-pool access for ``user`` and ``capability``.

    Raises ``HTTPException(403)`` when a non-admin user lacks both global worker
    access and an explicit override for ``capability``.
    """
    if not worker_access_applies(pool, user):
        return
    row = await db_module.get_user_worker_access(pool, user.sub)
    policy = policy_from_row(row)
    if is_worker_allowed(user, policy, capability):
        return
    if capability:
        detail = (
            f"worker pool access is required for capability '{capability}' "
            "(ask an administrator to enable worker access in the Admin Panel)"
        )
    else:
        detail = (
            "worker pool access is not enabled for your account "
            "(ask an administrator to enable worker access in the Admin Panel)"
        )
    raise HTTPException(status_code=403, detail=detail)
