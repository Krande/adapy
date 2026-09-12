"""Format-handler registry: one handler per job kind (``job.target_format``).

The worker's ``_process_one`` resolves a job to a handler and runs it inside the shared
pre/post steps (cancel + poison guards, cached-blob short-circuit, source download, conversion
settings, progress plumbing). A handler owns everything format-specific after that: it reports
the job's final status on the queue and patches the audit row itself, exactly as the branches
of the former dispatch chain did, so ``run`` returns nothing — the result IS those side effects
(queue status, audit row, uploaded blobs).

Two families:

* synthetic jobs (``needs_source = False``) have no source file — the model lives in postgres
  or the job carries its inputs in ``conversion_options`` — and run BEFORE any download;
* source-backed jobs (``needs_source = True``) get ``ctx.src_path`` (+ sidecars),
  ``ctx.on_progress``, the per-job ``ctx.settings`` and the fetch provenance ``ctx.fetch``.

The registry-backed converter path (``glb`` / ``ifc`` / ``step`` / ... via
``ConverterRegistry``) is one handler, registered as the fallback: any ``target_format`` no
synthetic handler claims reaches it, and an unknown one fails inside ``convert()`` with the
same ``UnsupportedFormat`` message the chain produced.

This module is a leaf (no worker imports) so both packages can import it without a cycle;
``ada.comms.rest.formats`` populates it.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable, Protocol

if TYPE_CHECKING:
    import asyncpg

    from ..queue import Job, JobQueue
    from ..scope import Scope
    from ..storage import Storage
    from ..worker.blobs import SourceFetch
    from ..worker.settings import ConversionSettings

# Progress contract a handler forwards to the queue: (stage, fraction 0..1).
ProgressCb = Callable[[str, float], Awaitable[None]]


@dataclass
class JobContext:
    """Everything the shared pre-steps prepared for a handler.

    ``src_path`` / ``on_progress`` / ``settings`` / ``fetch`` are populated only for
    source-backed handlers (``needs_source = True``); synthetic handlers see them as ``None``.
    """

    scope: Scope
    storage: Storage
    queue: JobQueue
    db_pool: asyncpg.Pool | None
    started_at: float
    src_path: pathlib.Path | None = None
    on_progress: ProgressCb | None = None
    settings: ConversionSettings | None = None
    fetch: SourceFetch | None = None


class FormatHandler(Protocol):
    """One job kind. ``kind`` is the ``target_format`` it claims (``"convert"`` for the
    registry-backed fallback, which claims everything else)."""

    kind: str
    needs_source: bool
    # Bypass the "derived blob already exists" short-circuit: for a handler whose real output
    # is a side effect (a DB write), not the derived blob.
    skip_cached_short_circuit: bool

    def supports(self, job: Job) -> bool: ...

    async def run(self, job: Job, ctx: JobContext) -> None: ...


class SyntheticFormatHandler:
    """Base for a handler of one synthetic (sourceless) job kind."""

    kind: str = ""
    needs_source: bool = False
    skip_cached_short_circuit: bool = False

    def supports(self, job: Job) -> bool:
        return job.target_format == self.kind

    async def run(self, job: Job, ctx: JobContext) -> None:
        raise NotImplementedError


class SourceFormatHandler(SyntheticFormatHandler):
    """Base for a handler that needs the source streamed to a local path first."""

    needs_source: bool = True


class UnknownJobKind(LookupError):
    """No registered handler claims the job (only possible before the fallback is registered)."""


_HANDLERS: list[FormatHandler] = []
_FALLBACK: FormatHandler | None = None


def register(handler: FormatHandler, *, fallback: bool = False) -> FormatHandler:
    """Add ``handler`` to the registry; a later registration of the same ``kind`` replaces the
    earlier one. ``fallback`` marks the catch-all consulted after every other handler."""
    global _FALLBACK
    if fallback:
        _FALLBACK = handler
        return handler
    _HANDLERS[:] = [h for h in _HANDLERS if h.kind != handler.kind]
    _HANDLERS.append(handler)
    return handler


def resolve(job: Job) -> FormatHandler:
    """The handler for ``job`` — first registered handler whose ``supports`` says yes, else the
    fallback."""
    for handler in _HANDLERS:
        if handler.supports(job):
            return handler
    if _FALLBACK is not None and _FALLBACK.supports(job):
        return _FALLBACK
    raise UnknownJobKind(f"no format handler registered for target_format {job.target_format!r}")


def handlers() -> list[FormatHandler]:
    """Every registered handler in resolution order (fallback last)."""
    return [*_HANDLERS] + ([_FALLBACK] if _FALLBACK is not None else [])


def registered_kinds() -> list[str]:
    return [h.kind for h in handlers()]


def synthetic_kinds() -> frozenset[str]:
    """The ``target_format`` values of every registered sourceless handler.

    A synthetic job carries no source file, so anything that reasons about a job's source
    extension (the worker's misroute guard) must exempt exactly these kinds — derived here so a
    newly registered synthetic handler cannot be forgotten in a hand-kept list.
    """
    return frozenset(h.kind for h in _HANDLERS if not h.needs_source)
