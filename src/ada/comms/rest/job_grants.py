"""Job grants: per-job, short-lived storage access for remote workers.

A worker outside the cluster should not hold storage keys: bucket-wide keys on a machine nobody
manages are the residual risk ``deploy/worker-trust.md`` ends on. Instead, when it picks up a
job, it asks the API for a grant over NATS, and reads and writes that job's files through the
API's job-scoped storage routes (``routes/job_storage.py``) with it.

The request
    ``<prefix>.<capability>.<worker name>`` (``ada.viewer.grants.sim-x.ws-01``), body
    ``{"job_id": "..."}``, from a connection with a private inbox prefix (``_INBOX_<worker
    name>``). The worker's NATS key is allowed to publish only its own capability and name, so
    the subject itself says who is asking and for which pool: NATS authenticates it, no adapy code
    has to.

The responder (:class:`GrantService`, a queue group on the API's own connection) checks, in order:
    the job exists and has not ended; its kind is one that can be granted (simulation runs, for
    now -- see :data:`PLUGIN_JOBS_REFUSAL`); the subject's capability is the job's, COMPUTED
    from the job (``sim-<provider id>``), never read from the worker registry, which any worker
    key can write; and the job is bound to this worker name, first come first bound -- a second
    worker asking for the same job is refused, until the holder has not asked for a whole grant
    lifetime (it stopped; its job was redelivered). ``{"job_id": "probe"}`` answers "no such job"
    without looking anything up: a worker's preflight uses it to prove its key may publish there
    and receive the reply.

The grant
    an HS256 JWT signed with ``ADA_VIEWER_WORKER_GRANT_SECRET`` and an issuer of its own
    (:data:`GRANT_ISSUER`), with ``{job_id, scope, cap, worker, iat, exp}``; ``exp`` is 15 minutes
    out by default. The user-facing auth refuses it (wrong issuer, wrong key), and it refuses
    every other token. A worker refreshes it at two thirds of its life with the same request; a
    job that has ended gets no refresh, and taking the worker's key out of ``nats.conf`` revokes
    it within one lifetime.

What a grant lets a worker do is NOT in the token. Every route call recomputes it from the job
and its run record (:func:`job_access`): read the record, the run's inputs, the cancel marker and
the provider's declared read prefixes; write under ``_simulations/<run_id>/``. A token for one
job is useless on another's routes, and a change to the run record (an input added) is seen at
once.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

import jwt

from ada.config import logger

from .config import WorkerGrantsConfig
from .queue import Job, capability_token
from .scope import Scope

__all__ = [
    "GRANT_ISSUER",
    "PLUGIN_JOBS_REFUSAL",
    "PROBE_JOB_ID",
    "GrantClaims",
    "GrantRefused",
    "GrantService",
    "GrantTokenError",
    "JobAccess",
    "KvGrantBindings",
    "MemoryGrantBindings",
    "job_access",
    "job_capability",
    "mint_grant",
    "safe_key",
    "scope_of_job",
    "verify_grant",
]

#: The ``iss`` of a job grant. Distinct from the CLI token's and from any IdP's, so the user-facing
#: auth never mistakes one for a user and the grant check never mistakes a user token for a grant.
GRANT_ISSUER = "ada-viewer-worker-grant"
#: The job id a worker's preflight asks for. Never a job: the answer is always "no such job".
PROBE_JOB_ID = "probe"

_TERMINAL = frozenset({"done", "error", "cancelled"})
#: How long a binding lasts without its holder asking again, when the caller does not say.
DEFAULT_STALE_S = 15 * 60
_SIMULATION_RUN = "simulation_run"
_PLUGIN_JOB = "plugin_job"

#: Why a plugin job gets no grant, said to the worker that asked.
#:
#: A plugin's capability is whatever a worker registry row says it is, and while the registry shares
#: the jobs bucket any worker key that can report job progress can write any registry row
#: (``deploy/worker-trust.md`` §2). Granting by that capability would let a key that serves one pool
#: claim another plugin's jobs and their outputs. A simulation run's capability is computed from its
#: provider id, which the API wrote, so it has no such hole.
PLUGIN_JOBS_REFUSAL = (
    "plugin jobs are not granted storage yet: a plugin's capability is read from the worker registry, which "
    "any worker key can write while the registry shares the jobs bucket. They will be once the registry has a "
    "bucket of its own with keys pinned to their own rows, or an allowlist maps capabilities to plugin ids."
)

# Refusal codes, as the worker reads them.
NO_SUCH_JOB = "no_such_job"
JOB_FINISHED = "job_finished"
KIND_NOT_GRANTED = "kind_not_granted"
CAPABILITY_MISMATCH = "capability_mismatch"
CLAIMED = "claimed_by_another_worker"
RECORD_MISSING = "run_record_missing"
BAD_REQUEST = "bad_request"
INTERNAL = "internal_error"


class GrantRefused(Exception):
    """The API will not grant (or no longer allows) this. ``code`` is one of the refusal codes."""

    def __init__(self, code: str, detail: str, *, status: int = 403) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status

    def as_reply(self) -> dict:
        return {"ok": False, "error": self.code, "detail": self.detail}


class GrantTokenError(Exception):
    """A bearer that is not a valid, current job grant."""


# ── the token ────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GrantClaims:
    job_id: str
    scope: str
    cap: str
    worker: str
    iat: int
    exp: int


def mint_grant(cfg: WorkerGrantsConfig, *, job_id: str, scope: str, cap: str, worker: str, now: float | None = None):
    """``(token, exp)``: a job grant for ``worker`` on ``job_id``, valid ``cfg.ttl_s`` seconds."""
    if not cfg.secret:
        raise RuntimeError("job grants are disabled (ADA_VIEWER_WORKER_GRANT_SECRET unset)")
    iat = int(now if now is not None else time.time())
    exp = iat + int(cfg.ttl_s)
    claims = {
        "iss": GRANT_ISSUER,
        "job_id": job_id,
        "scope": scope,
        "cap": cap,
        "worker": worker,
        "iat": iat,
        "exp": exp,
    }
    return jwt.encode(claims, cfg.secret, algorithm="HS256"), exp


def verify_grant(cfg: WorkerGrantsConfig, token: str, *, now: float | None = None) -> GrantClaims:
    """The claims of a valid, unexpired job grant; :class:`GrantTokenError` for anything else."""
    if not cfg.secret:
        raise GrantTokenError("job grants are disabled on this server")
    options: dict[str, Any] = {"require": ["exp", "iat", "iss", "job_id", "scope", "cap", "worker"]}
    if now is not None:
        # PyJWT compares against the wall clock; a test passes the instant it means instead.
        options["verify_exp"] = False
    try:
        claims = jwt.decode(token, cfg.secret, algorithms=["HS256"], issuer=GRANT_ISSUER, options=options)
    except jwt.ExpiredSignatureError as exc:
        raise GrantTokenError("job grant expired; ask for a new one") from exc
    except jwt.InvalidIssuerError as exc:
        raise GrantTokenError("not a job grant") from exc
    except jwt.InvalidTokenError as exc:
        raise GrantTokenError(f"invalid job grant: {exc}") from exc
    if now is not None and int(claims["exp"]) <= now:
        raise GrantTokenError("job grant expired; ask for a new one")
    for name in ("job_id", "scope", "cap", "worker"):
        if not isinstance(claims.get(name), str) or not claims[name]:
            raise GrantTokenError(f"invalid job grant: {name!r} is not a string")
    return GrantClaims(
        job_id=claims["job_id"],
        scope=claims["scope"],
        cap=claims["cap"],
        worker=claims["worker"],
        iat=int(claims["iat"]),
        exp=int(claims["exp"]),
    )


# ── what a job may read and write ────────────────────────────────────────────────────────────────

_UNSAFE_KEY = re.compile(r"[\x00-\x1f\x7f\\]")


def safe_key(raw: object, *, prefix: bool = False) -> str:
    """``raw`` as a scope-relative storage key, or ValueError: no leading ``/``, no ``.`` or ``..``
    segment, no empty segment, no control character or backslash. ``prefix=True`` allows (and
    keeps) a trailing ``/`` and the empty prefix."""
    if not isinstance(raw, str):
        raise ValueError("a key must be a string")
    key = raw.lstrip("/")
    if _UNSAFE_KEY.search(key) or len(key) > 1024:
        raise ValueError(f"not a storage key: {raw!r}")
    if prefix and key == "":
        return key
    body = key[:-1] if prefix and key.endswith("/") else key
    if not body or any(seg in ("", ".", "..") for seg in body.split("/")):
        raise ValueError(f"not a storage key: {raw!r}")
    return key


def _declared_prefix(raw: object, provider_id: str) -> str | None:
    """A provider's read prefix, checked: a directory (trailing ``/`` added), not an internal
    namespace (``_simulations/``, ``_derived/`` -- another run's files are not a read prefix), not
    the whole scope. None (logged) for one that is not usable."""
    try:
        key = safe_key(raw, prefix=True)
    except ValueError:
        key = ""
    key = key if key.endswith("/") or not key else key + "/"
    if not key or key.startswith("_"):
        logger.warning("job grants: ignoring read prefix %r declared for provider %s", raw, provider_id)
        return None
    return key


@dataclass(frozen=True)
class JobAccess:
    """What one job's grant allows, recomputed per call from the job and its run record."""

    job_id: str
    kind: str
    scope: Scope
    capability: str
    run_id: str
    provider_id: str
    #: Exact keys the job may read.
    read_keys: frozenset[str]
    #: Key prefixes (each ending in ``/``) the job may read and list.
    read_prefixes: tuple[str, ...]
    #: Key prefixes the job may write (and read and list).
    write_prefixes: tuple[str, ...]

    def can_write(self, key: str) -> bool:
        return any(key.startswith(p) for p in self.write_prefixes)

    def can_read(self, key: str) -> bool:
        return key in self.read_keys or self.can_write(key) or any(key.startswith(p) for p in self.read_prefixes)

    def can_list(self, prefix: str) -> bool:
        """A listing of ``prefix`` stays inside what the job may read: the prefix is under one of
        its read or write prefixes. (Listing a parent of an exact input key is not allowed; the job
        reads that key by name.)"""
        if not prefix:
            return False
        return any(prefix.startswith(p) for p in (*self.read_prefixes, *self.write_prefixes))

    def describe(self) -> dict:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "scope": self.scope.prefix(),
            "capability": self.capability,
            "run_id": self.run_id,
            "read_keys": sorted(self.read_keys),
            "read_prefixes": list(self.read_prefixes),
            "write_prefixes": list(self.write_prefixes),
        }


def scope_of_job(job: Job) -> Scope:
    if job.scope_kind in ("shared", "providers"):
        return Scope(kind=job.scope_kind)  # type: ignore[arg-type]
    return Scope(kind=job.scope_kind, id=job.scope_id)  # type: ignore[arg-type]


def _simulation_ids(job: Job) -> tuple[str, str]:
    from ada.simulation.interfaces import PROVIDER_ID_PATTERN
    from ada.simulation.runs import run_record_key

    opts = job.conversion_options or {}
    provider_id, run_id = opts.get("provider_id"), opts.get("run_id")
    if not isinstance(provider_id, str) or not PROVIDER_ID_PATTERN.match(provider_id):
        raise GrantRefused(KIND_NOT_GRANTED, "the job names no valid simulation provider")
    try:
        record_key = run_record_key(str(run_id))
    except ValueError:
        raise GrantRefused(KIND_NOT_GRANTED, "the job names no valid simulation run") from None
    if job.derived_key != record_key:
        # The API writes the record at the job's derived key; a job that says otherwise is not one
        # it submitted as a run.
        raise GrantRefused(KIND_NOT_GRANTED, "the job's derived key is not its run record")
    return provider_id, str(run_id)


def job_capability(job: Job) -> str:
    """The capability a job's grant is for, COMPUTED from the job: a simulation run's is
    ``sim-<provider id>`` (``ada.simulation.provider_capability``). Never the worker registry's
    word for it, and never ``job.target_capability`` alone, which the API copied from the
    registry when it submitted the job. :class:`GrantRefused` for a kind that gets no grant."""
    from ada.simulation.interfaces import provider_capability

    if job.target_format == _SIMULATION_RUN:
        provider_id, _ = _simulation_ids(job)
        return capability_token(provider_capability(provider_id))
    if job.target_format == _PLUGIN_JOB:
        raise GrantRefused(KIND_NOT_GRANTED, PLUGIN_JOBS_REFUSAL)
    raise GrantRefused(KIND_NOT_GRANTED, f"jobs of kind {job.target_format!r} are not granted storage")


def provider_read_prefixes(provider_id: str, cfg: WorkerGrantsConfig) -> tuple[str, ...]:
    """The read prefixes of a provider's runs: what its code declared
    (``ada.simulation.declare_storage_read_prefixes``) and what the deployment adds
    (``ADA_VIEWER_WORKER_GRANT_READ_PREFIXES``), each checked."""
    from ada.simulation.registry import storage_read_prefixes

    raw = [*storage_read_prefixes(provider_id), *(cfg.read_prefixes.get(provider_id) or [])]
    out: list[str] = []
    for prefix in raw:
        checked = _declared_prefix(prefix, provider_id)
        if checked and checked not in out:
            out.append(checked)
    return tuple(out)


async def job_access(job: Job, storage, cfg: WorkerGrantsConfig) -> JobAccess:
    """What ``job``'s grant allows NOW: computed from the job and (for a simulation run) its run
    record, which the API reads itself. :class:`GrantRefused` when the job gets no grant."""
    from ada.simulation.runs import (
        RunRecordError,
        cancel_key,
        parse_run_record,
        run_prefix,
        run_record_key,
    )

    capability = job_capability(job)  # refuses every kind but a simulation run
    provider_id, run_id = _simulation_ids(job)
    scope = scope_of_job(job)
    record_key = run_record_key(run_id)
    try:
        record = parse_run_record(await storage.get_bytes(scope, record_key))
    except (FileNotFoundError, KeyError):
        raise GrantRefused(RECORD_MISSING, f"simulation run {run_id} has no run record", status=404) from None
    except RunRecordError as exc:
        raise GrantRefused(RECORD_MISSING, f"simulation run {run_id}: unreadable run record ({exc})") from None
    except Exception as exc:  # obstore's not-found is not always a FileNotFoundError
        if "not found" in str(exc).lower():
            raise GrantRefused(RECORD_MISSING, f"simulation run {run_id} has no run record", status=404) from None
        raise
    if (record.get("provider") or {}).get("id") != provider_id:
        raise GrantRefused(KIND_NOT_GRANTED, "the run record names another provider than the job")
    inputs = set()
    for key in record.get("inputs") or []:
        try:
            inputs.add(safe_key(key))
        except ValueError:
            logger.warning("job grants: run %s lists an input that is not a storage key: %r", run_id, key)
    return JobAccess(
        job_id=job.job_id,
        kind=job.target_format,
        scope=scope,
        capability=capability,
        run_id=run_id,
        provider_id=provider_id,
        read_keys=frozenset({record_key, cancel_key(run_id), *inputs}),
        read_prefixes=provider_read_prefixes(provider_id, cfg),
        write_prefixes=(run_prefix(run_id),),
    )


# ── who holds a job ──────────────────────────────────────────────────────────────────────────────


class GrantBindings(Protocol):
    async def claim(self, job_id: str, worker: str, cap: str, *, stale_after_s: float) -> str:
        """Bind ``job_id`` to ``worker`` if nothing holds it, or if its holder has not asked for
        ``stale_after_s`` (it stopped: crashed, lost the bus); return whoever holds it now. The
        holder asking again refreshes the binding."""

    async def holder(self, job_id: str) -> str | None:
        """The worker the job is bound to, or None."""


def _claimed(held: dict | None, worker: str, cap: str, now: float, stale_after_s: float) -> dict | None:
    """The binding to store for ``worker``'s request, or None when another worker keeps the job."""
    if held is None or held.get("worker") == worker:
        bound_at = held.get("bound_at", now) if held else now
        return {"worker": worker, "cap": cap, "bound_at": bound_at, "seen_at": now}
    if now - float(held.get("seen_at") or held.get("bound_at") or 0.0) > stale_after_s:
        # Its holder has not refreshed for a whole grant lifetime, so its grant has expired and the
        # job came back to the queue: the new worker takes it over, and the old one's grant (were
        # it alive after all) no longer matches the binding.
        return {"worker": worker, "cap": cap, "bound_at": now, "seen_at": now, "took_over_from": held.get("worker")}
    return None


class MemoryGrantBindings:
    """Bindings in this process: for tests, and a single-replica API without a bucket."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._held: dict[str, dict] = {}
        self._clock = clock

    async def claim(self, job_id: str, worker: str, cap: str, *, stale_after_s: float = DEFAULT_STALE_S) -> str:
        new = _claimed(self._held.get(job_id), worker, cap, self._clock(), stale_after_s)
        if new is not None:
            self._held[job_id] = new
        return self._held[job_id]["worker"]

    async def holder(self, job_id: str) -> str | None:
        held = self._held.get(job_id)
        return held["worker"] if held else None


class KvGrantBindings:
    """Bindings in a KV bucket of their own (``WorkerGrantsConfig.kv_bucket``), so every API
    replica agrees and no worker key can write one: the bucket is the API's, and a worker's
    permissions name only the jobs bucket. ``create`` and ``update`` are compare-and-set, so of two
    workers racing for one job exactly one is bound. Entries expire with the bucket's TTL."""

    def __init__(self, kv, clock: Callable[[], float] = time.time) -> None:
        self._kv = kv
        self._clock = clock

    async def _entry(self, job_id: str):
        from nats.js.errors import KeyNotFoundError

        try:
            entry = await self._kv.get(job_id)
        except KeyNotFoundError:
            return None, None
        if entry is None or not entry.value:
            return entry, None
        try:
            value = json.loads(entry.value.decode("utf-8"))
        except (ValueError, AttributeError):
            value = None
        return entry, value if isinstance(value, dict) else None

    async def claim(self, job_id: str, worker: str, cap: str, *, stale_after_s: float = DEFAULT_STALE_S) -> str:
        from nats.js.errors import KeyWrongLastSequenceError

        for _ in range(3):
            entry, held = await self._entry(job_id)
            new = _claimed(held, worker, cap, self._clock(), stale_after_s)
            if new is None:
                return str(held.get("worker"))
            body = json.dumps(new).encode("utf-8")
            try:
                if entry is None:
                    await self._kv.create(job_id, body)
                else:
                    await self._kv.update(job_id, body, last=entry.revision)
                return worker
            except KeyWrongLastSequenceError:
                continue  # another replica wrote it first: read again
        held_now = await self.holder(job_id)
        return held_now if held_now is not None else worker

    async def holder(self, job_id: str) -> str | None:
        _, held = await self._entry(job_id)
        return str(held.get("worker") or "") or None if held else None


# ── the responder and the per-call check ─────────────────────────────────────────────────────────


#: A worker name or a capability: one subject token.
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$")


class GrantService:
    """Answers grant requests and checks grant bearers. One per API process (``app.state``).

    ``get_job`` is the queue's job lookup; ``bindings`` where job -> worker bindings live;
    ``pool`` (a callable, as the pool appears after startup) the database for audit rows.
    """

    #: The queue group every API replica's responder joins, so each request is answered once.
    QUEUE_GROUP = "ada-viewer-grants"

    def __init__(
        self,
        cfg: WorkerGrantsConfig,
        storage,
        get_job: Callable[[str], Awaitable[Job | None]],
        bindings: GrantBindings,
        *,
        pool: Callable[[], Any] = lambda: None,
    ) -> None:
        self.cfg = cfg
        self.storage = storage
        self._get_job = get_job
        self.bindings = bindings
        self._pool = pool
        self._sub = None

    # -- the NATS side ------------------------------------------------------------------------------

    async def start(self, queue) -> None:
        """Subscribe ``<prefix>.*.*`` in the queue group, on the API's connection."""

        async def on_request(msg) -> None:
            try:
                reply = await self.answer(msg.subject, msg.data)
            except Exception:  # noqa: BLE001 - the worker gets an answer, the log the traceback
                logger.exception("job grants: answering %s failed", msg.subject)
                reply = {"ok": False, "error": INTERNAL, "detail": "the API could not answer; try again"}
            try:
                await msg.respond(json.dumps(reply).encode("utf-8"))
            except Exception:  # noqa: BLE001 - a requester that gave up
                logger.debug("job grants: could not respond on %s", msg.subject, exc_info=True)

        subject = f"{self.cfg.subject_prefix}.*.*"
        self._sub = await queue.subscribe(subject, queue=self.QUEUE_GROUP, cb=on_request)
        logger.info("job grants: answering %s (grant lifetime %ds)", subject, self.cfg.ttl_s)

    async def stop(self) -> None:
        if self._sub is not None:
            try:
                await self._sub.unsubscribe()
            except Exception:  # noqa: BLE001 - a closing connection
                pass
            self._sub = None

    def parse_subject(self, subject: str) -> tuple[str, str]:
        """``(capability, worker)`` of a grant subject, or :class:`GrantRefused`."""
        head = f"{self.cfg.subject_prefix}."
        rest = subject[len(head) :] if subject.startswith(head) else ""
        parts = rest.split(".")
        if len(parts) != 2 or not all(_TOKEN.match(p) for p in parts):
            raise GrantRefused(BAD_REQUEST, f"not a grant subject: {subject!r} (<prefix>.<capability>.<worker name>)")
        return parts[0], parts[1]

    async def answer(self, subject: str, data: bytes) -> dict:
        """The reply to one grant request: ``{"ok": true, "token", ...}`` or a refusal."""
        try:
            cap, worker = self.parse_subject(subject)
            try:
                body = json.loads(data or b"{}")
            except ValueError:
                raise GrantRefused(BAD_REQUEST, "the request body is not JSON") from None
            job_id = body.get("job_id") if isinstance(body, dict) else None
            if not isinstance(job_id, str) or not job_id:
                raise GrantRefused(BAD_REQUEST, 'the request needs {"job_id": "..."}')
            return await self.grant(cap, worker, job_id)
        except GrantRefused as exc:
            return exc.as_reply()

    async def grant(self, cap: str, worker: str, job_id: str) -> dict:
        if job_id == PROBE_JOB_ID:
            # The preflight's question: "may I ask, and do I hear the answer?" Nothing is looked up.
            return {**GrantRefused(NO_SUCH_JOB, "no such job (probe)").as_reply(), "probe": True}
        job = await self._get_job(job_id)
        if job is None:
            raise GrantRefused(NO_SUCH_JOB, f"no such job: {job_id}", status=404)
        try:
            if job.status in _TERMINAL:
                raise GrantRefused(JOB_FINISHED, f"job {job_id} has ended ({job.status}); no grant is issued for it")
            access = await job_access(job, self.storage, self.cfg)
            if cap != access.capability:
                raise GrantRefused(
                    CAPABILITY_MISMATCH,
                    f"job {job_id} is for capability {access.capability!r}, not {cap!r}",
                )
            holder = await self.bindings.claim(job_id, worker, cap, stale_after_s=self.cfg.ttl_s)
            if holder != worker:
                raise GrantRefused(CLAIMED, f"job {job_id} is bound to another worker")
        except GrantRefused as exc:
            await self.audit("job_grant", job, worker, status="refused", error=f"{exc.code}: {exc.detail}")
            raise
        token, exp = mint_grant(self.cfg, job_id=job_id, scope=access.scope.prefix(), cap=cap, worker=worker)
        await self.audit("job_grant", job, worker, status="ok")
        logger.info("job grants: granted job %s to worker %s (%s)", job_id, worker, cap)
        return {
            "ok": True,
            "token": token,
            "expires_at": exp,
            "ttl_s": self.cfg.ttl_s,
            "job_id": job_id,
            "scope": access.scope.prefix(),
            "cap": cap,
            "worker": worker,
            "storage_path": f"/api/jobs/{job_id}/storage",
        }

    async def authorise(self, token: str, job_id: str) -> tuple[GrantClaims, JobAccess, Job]:
        """Check a bearer against the job it is used on, from scratch: the token is a valid grant
        FOR THIS JOB, the job still exists, and its scope, capability and binding are still what
        the grant says. :class:`GrantTokenError` (401) or :class:`GrantRefused` otherwise."""
        claims = verify_grant(self.cfg, token)
        if claims.job_id != job_id:
            raise GrantRefused(BAD_REQUEST, "this grant is for another job")
        job = await self._get_job(job_id)
        if job is None:
            raise GrantRefused(NO_SUCH_JOB, f"no such job: {job_id}", status=404)
        access = await job_access(job, self.storage, self.cfg)
        if claims.scope != access.scope.prefix() or claims.cap != access.capability:
            raise GrantRefused(BAD_REQUEST, "this grant no longer matches the job")
        if await self.bindings.holder(job_id) != claims.worker:
            raise GrantRefused(CLAIMED, f"job {job_id} is bound to another worker")
        # Deliberately NOT refused for a job that has ended: the cancel route marks the job
        # cancelled before its worker has written the record that says so, and that last write is
        # what the user is waiting for. A grant that outlives its job does so by one lifetime at
        # most, because an ended job gets no refresh.
        return claims, access, job

    async def audit(
        self,
        action: str,
        job: Job | None,
        worker: str,
        *,
        key: str | None = None,
        status: str | None = None,
        error: str | None = None,
        write_bytes: int | None = None,
    ) -> None:
        """One audit row, as ``worker:<name>``. Best-effort: no pool, no row."""
        pool = self._pool()
        if pool is None or job is None:
            return
        from . import db as db_module

        try:
            await db_module.insert_audit(
                pool,
                user_sub=f"worker:{worker}",
                scope_kind=job.scope_kind,
                scope_id=job.scope_id,
                action=action,
                key=key,
                target_format=job.target_format,
                status=status,
                error=error,
                job_id=job.job_id,
                write_bytes=write_bytes,
            )
        except Exception:  # noqa: BLE001 - an audit row never fails the call it describes
            logger.debug("job grants: audit row %s for job %s failed", action, job.job_id, exc_info=True)
