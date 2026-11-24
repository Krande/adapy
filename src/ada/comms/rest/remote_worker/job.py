"""A worker with no storage keys (``ADA_VIEWER_STORAGE_KIND=grants``): storage per job, by grant.

:class:`GrantModeStorage` stands where the worker's ``Storage`` would. It opens nothing; a job
kind that can be granted asks it for :meth:`GrantModeStorage.open_job`, which requests the job's
grant over the worker's NATS connection and returns a :class:`~.storage.RemoteGrantStorage` bound
to the job. Any other job kind reaching it fails with one sentence saying why, instead of an
attribute error from deep inside a handler.

:func:`active_grant_storage` hands provider code the facade of the run it is part of, for reads
beside ``SimulationContext`` (shared assets under a declared read prefix).
"""

from __future__ import annotations

import asyncio
import threading
from typing import Awaitable, Callable

from ..config import GrantStorageConfig
from .grants import GrantClient
from .storage import RemoteGrantStorage

__all__ = ["GrantModeStorage", "GrantModeUnsupported", "JobGrantSession", "active_grant_storage"]


class GrantModeUnsupported(RuntimeError):
    """A job kind this worker cannot run: it holds no storage keys, and the kind gets no grant."""


RequestFn = Callable[[str, bytes, float], Awaitable[bytes]]


class JobGrantSession:
    """One job's grant client and storage facade. Close it when the job ends."""

    def __init__(self, client: GrantClient, storage: RemoteGrantStorage, run_id: str | None) -> None:
        self.client = client
        self.storage = storage
        self.run_id = run_id

    def close(self) -> None:
        if self.run_id is not None:
            with _ACTIVE_LOCK:
                if _ACTIVE.get(self.run_id) is self.storage:
                    _ACTIVE.pop(self.run_id, None)
        self.storage.close()


class GrantModeStorage:
    """The worker's storage when it has none of its own (see the module docstring)."""

    kind = "grants"
    #: How long a redelivered job bound to another worker is waited for before it is left to that
    #: worker: longer than a grant lifetime, after which a stopped holder's binding lapses.
    claim_wait_s = 20 * 60
    claim_poll_s = 30.0

    def __init__(self, cfg: GrantStorageConfig, *, small_object_max: int = 8 * 1024 * 1024, client_factory=None):
        self.cfg = cfg
        self._small = small_object_max
        # For tests: () -> httpx.Client with base_url set (a FastAPI TestClient).
        self._client_factory = client_factory

    async def open_job(
        self,
        request: RequestFn,
        *,
        job_id: str,
        capability: str,
        loop: asyncio.AbstractEventLoop,
        run_id: str | None = None,
    ) -> JobGrantSession:
        """Ask for the job's grant (refusals raise ``GrantRefusedError``) and bind a facade to it."""
        client = GrantClient(
            request,
            subject_prefix=self.cfg.subject_prefix,
            capability=capability,
            worker_name=self.cfg.worker_name,
            job_id=job_id,
            timeout_s=self.cfg.request_timeout_s,
        )
        await client.grant()  # the first answer decides whether this worker runs the job at all
        http = self._client_factory() if self._client_factory is not None else None
        storage = RemoteGrantStorage(
            job_id,
            client.token_provider(loop),
            api_url=None if http is not None else self.cfg.api_url,
            client=http,
            small_object_max=self._small,
        )
        if run_id is not None:
            with _ACTIVE_LOCK:
                _ACTIVE[run_id] = storage
        return JobGrantSession(client, storage, run_id)

    def __getattr__(self, name: str):
        # Every Storage method a handler might call: refused, by name, with the reason.
        if name.startswith("_"):
            raise AttributeError(name)

        async def refused(*_args, **_kwargs):
            raise GrantModeUnsupported(
                f"this worker holds no storage keys (ADA_VIEWER_STORAGE_KIND=grants), so it can only run jobs "
                f"that are granted storage per job (simulation runs); Storage.{name} is not available here"
            )

        return refused


_ACTIVE: dict[str, RemoteGrantStorage] = {}
_ACTIVE_LOCK = threading.Lock()


def active_grant_storage(run_id: str) -> RemoteGrantStorage | None:
    """The facade of the simulation run ``run_id`` while this worker runs it under a grant, else
    None (a worker with storage keys, or a run that is not going here)."""
    with _ACTIVE_LOCK:
        return _ACTIVE.get(run_id)
