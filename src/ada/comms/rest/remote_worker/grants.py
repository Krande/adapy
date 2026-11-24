"""Asking for a job grant over NATS, and keeping it fresh.

``request`` is any coroutine ``(subject, payload, timeout) -> reply bytes``: the worker's
``JobQueue.request`` (a connection with the private inbox prefix), or a fake in a test.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

__all__ = [
    "Grant",
    "GrantClient",
    "GrantRefusedError",
    "GrantUnavailableError",
    "grant_subject",
]

RequestFn = Callable[[str, bytes, float], Awaitable[bytes]]

#: A grant is renewed once this much of its lifetime has passed.
REFRESH_FRACTION = 2 / 3


class GrantRefusedError(RuntimeError):
    """The API answered and said no. ``code`` is its refusal code (``no_such_job``,
    ``job_finished``, ``capability_mismatch``, ``claimed_by_another_worker``, ``kind_not_granted``,
    ``run_record_missing``, ``bad_request``, ``internal_error``)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"job grant refused ({code}): {detail}")
        self.code = code
        self.detail = detail


class GrantUnavailableError(RuntimeError):
    """Nobody answered: no responder on the subject (job grants are off on the API, or the subject
    is not one it serves), or the request timed out (often a NATS permission violation: a publish
    the server refused is dropped, not answered)."""


def grant_subject(prefix: str, capability: str, worker_name: str) -> str:
    return f"{prefix}.{capability}.{worker_name}"


@dataclass(frozen=True)
class Grant:
    token: str
    job_id: str
    scope: str
    cap: str
    worker: str
    expires_at: int
    ttl_s: int
    #: This machine's clock when the grant arrived: refresh is timed from it, so a clock that is
    #: off from the API's does not make a worker refresh too late.
    received_at: float

    @property
    def refresh_at(self) -> float:
        return self.received_at + self.ttl_s * REFRESH_FRACTION

    def __repr__(self) -> str:  # the token never goes to a log through a repr
        return f"Grant(job_id={self.job_id!r}, worker={self.worker!r}, cap={self.cap!r}, ttl_s={self.ttl_s})"


class GrantClient:
    """One job's grant: asked for once, renewed at two thirds of its lifetime with the same request.

    Thread-safe only on its own event loop; the synchronous storage facade reaches it through
    ``asyncio.run_coroutine_threadsafe`` (:meth:`token_provider`)."""

    def __init__(
        self,
        request: RequestFn,
        *,
        subject_prefix: str,
        capability: str,
        worker_name: str,
        job_id: str,
        timeout_s: float = 10.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._request = request
        self.subject = grant_subject(subject_prefix, capability, worker_name)
        self.job_id = job_id
        self._timeout = timeout_s
        self._clock = clock
        self._grant: Grant | None = None
        self._lock = asyncio.Lock()

    @property
    def current(self) -> Grant | None:
        return self._grant

    async def ask(self) -> Grant:
        """One request, its answer as a :class:`Grant`, or the refusal."""
        payload = json.dumps({"job_id": self.job_id}).encode("utf-8")
        try:
            raw = await self._request(self.subject, payload, self._timeout)
        except Exception as exc:  # noqa: BLE001 - nats-py's NoResponders / Timeout, named below
            name = type(exc).__name__
            if "NoResponders" in name:
                raise GrantUnavailableError(
                    f"no responder on {self.subject}: job grants are not enabled on the API"
                ) from exc
            if "Timeout" in name or isinstance(exc, asyncio.TimeoutError):
                raise GrantUnavailableError(
                    f"no answer on {self.subject} in {self._timeout:.0f}s (a NATS permission violation drops the "
                    "request silently: check the key may publish this subject and subscribe its private inbox)"
                ) from exc
            raise
        try:
            reply = json.loads(raw)
        except ValueError as exc:
            raise GrantUnavailableError(f"the answer on {self.subject} is not JSON") from exc
        if not isinstance(reply, dict) or not reply.get("ok"):
            reply = reply if isinstance(reply, dict) else {}
            raise GrantRefusedError(str(reply.get("error") or "refused"), str(reply.get("detail") or ""))
        return Grant(
            token=str(reply["token"]),
            job_id=str(reply.get("job_id") or self.job_id),
            scope=str(reply.get("scope") or ""),
            cap=str(reply.get("cap") or ""),
            worker=str(reply.get("worker") or ""),
            expires_at=int(reply.get("expires_at") or 0),
            ttl_s=int(reply.get("ttl_s") or 0),
            received_at=self._clock(),
        )

    async def grant(self, *, force: bool = False) -> Grant:
        """The current grant, renewed first when two thirds of it have passed (or ``force``).

        A renewal that fails while the grant still has life left keeps the grant: the API stops
        renewing a job that has ended, and the run's last writes (its final record) still have to
        land. ``force`` (the API refused the grant) always asks, and raises if the answer is no."""
        async with self._lock:
            grant = self._grant
            if force or grant is None or self._clock() >= grant.refresh_at:
                try:
                    self._grant = grant = await self.ask()
                except (GrantRefusedError, GrantUnavailableError):
                    if force or grant is None or self._clock() >= grant.received_at + grant.ttl_s - 30:
                        raise
            return grant

    async def token(self, *, force: bool = False) -> str:
        return (await self.grant(force=force)).token

    def token_provider(self, loop: asyncio.AbstractEventLoop) -> Callable[..., str]:
        """A synchronous ``(force=False) -> token`` for a worker thread, bridged onto ``loop``."""

        def provide(force: bool = False) -> str:
            return asyncio.run_coroutine_threadsafe(self.token(force=force), loop).result()

        return provide
