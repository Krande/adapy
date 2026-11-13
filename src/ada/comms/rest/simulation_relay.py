"""Hand a simulation run off to a remote Ada Studio.

A studio with no simulation workers of its own (``ada dev connections``: Docker and ROS are not on a
developer's machine) can be pointed at one that has them (``ADA_SIMULATION_REMOTE_URL``, ``_TOKEN``,
``_SCOPE``). The local API then

* lists the remote's providers beside its own, each marked ``remote: true`` and keeping its
  ``accepts`` (a local provider with the same id wins);
* starts a run on a remote provider by uploading the run's input files (the subject included) to the
  remote scope under the same keys, starting the run there, and writing a LOCAL run record with the
  remote's run id (so every artefact key is the same on both sides) and a ``remote`` field,
  ``{url, scope, run_id}``;
* answers ``GET`` run / list, ``cancel`` and the artefact fetch (byte ranges included) of such a run
  from the remote, refreshing the local record from what the remote says.

The viewer needs no change: a relayed run reads like any other. Every failure to reach the remote, and
every refusal by it, is a 502 that names it. Without ``simulation_remote`` in the settings none of this
exists (``app.state.simulation_relay`` is None).

LIGHT, like :mod:`.simulation_runs`: httpx, ``ada.simulation`` and the storage; no format handler.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from ada.config import logger
from ada.simulation.interfaces import SIMULATION_DOMAINS
from ada.simulation.runs import TERMINAL_STATUSES, RunRecordError, parse_run_record

from .config import SimulationRemote
from .scope import Scope
from .simulation_runs import _provider_entry, _sorted, write_run

__all__ = ["SimulationRelay", "merge_providers", "relay_of"]

#: How long the remote's provider listing is reused: the panel polls it, the remote need not hear it each time.
PROVIDERS_TTL_S = 10.0
#: Seconds to connect to the remote / to wait on one of its answers. Uploads and artefact bodies
#: stream, so this is per read, not per transfer.
CONNECT_TIMEOUT_S = 10.0
READ_TIMEOUT_S = 60.0

#: Headers of the remote's artefact answer that are passed on to the viewer: what a ranged read needs.
_PASS_HEADERS = ("content-range", "content-encoding", "content-length", "accept-ranges", "content-type")


def relay_of(request) -> "SimulationRelay | None":
    """The app's relay, or None when no remote is configured."""
    return getattr(request.app.state, "simulation_relay", None)


def merge_providers(local: list[dict], remote: list[dict]) -> list[dict]:
    """``local`` and ``remote`` providers in one listing. A remote one never shadows a local one: if
    the ids collide the local one is kept and the clash is logged."""
    have = {p["id"] for p in local}
    extra = []
    for p in remote:
        if p["id"] in have:
            logger.warning("simulation relay: remote provider %r is also registered here; the local one is used", p["id"])
            continue
        extra.append(p)
    return _sorted([*local, *extra])


class SimulationRelay:
    """The client of one remote Ada Studio's simulation routes."""

    def __init__(self, remote: SimulationRemote, *, transport: httpx.AsyncBaseTransport | None = None):
        self.remote = remote
        self.url = remote.url.rstrip("/")
        self._transport = transport
        self._providers: tuple[float, list[dict], str | None] | None = None

    # -- the wire ------------------------------------------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        headers = {"Authorization": f"Bearer {self.remote.token}"} if self.remote.token else {}
        return httpx.AsyncClient(
            base_url=self.url,
            headers=headers,
            transport=self._transport,
            timeout=httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
        )

    def _fail(self, what: str, detail: Any) -> HTTPException:
        return HTTPException(status_code=502, detail=f"remote Ada Studio {self.url}: {what}: {detail}")

    async def _call(self, method: str, path: str, *, what: str, ok=(200, 201), **kwargs) -> httpx.Response:
        try:
            async with self._client() as client:
                r = await client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise self._fail(what, f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__) from exc
        if r.status_code not in ok:
            raise self._fail(what, f"HTTP {r.status_code} {_detail(r)}")
        return r

    def _scope_path(self) -> str:
        return f"/api/scopes/{quote(self.remote.scope, safe='')}"

    # -- providers -----------------------------------------------------------------------------

    async def providers(self) -> tuple[list[dict], str | None]:
        """The remote's providers as this API lists them (``remote: true``), and why there are none
        when the remote cannot be asked. Reused for :data:`PROVIDERS_TTL_S`."""
        now = time.monotonic()
        if self._providers is not None and now - self._providers[0] < PROVIDERS_TTL_S:
            return self._providers[1], self._providers[2]
        out: list[dict] = []
        error: str | None = None
        try:
            r = await self._call("GET", "/api/simulation/providers", what="listing providers", ok=(200,))
            for spec in r.json().get("providers") or []:
                if not isinstance(spec, dict) or not isinstance(spec.get("id"), str) or not spec["id"]:
                    continue
                if spec.get("domain") not in SIMULATION_DOMAINS:
                    continue
                entry = _provider_entry(spec, online=bool(spec.get("online")), workers=int(spec.get("workers") or 0))
                if spec.get("unavailable_reason"):
                    entry["unavailable_reason"] = spec["unavailable_reason"]
                entry["remote"] = True
                out.append(entry)
        except HTTPException as exc:
            error = str(exc.detail)
            logger.warning("simulation relay: %s", error)
        except ValueError as exc:  # not JSON
            error = f"remote Ada Studio {self.url}: listing providers: not JSON ({exc})"
            logger.warning("simulation relay: %s", error)
        self._providers = (now, out, error)
        return out, error

    # -- runs ----------------------------------------------------------------------------------

    async def start(
        self,
        storage,
        scope: Scope,
        *,
        provider_id: str,
        parameters: dict,
        kind: str | None,
        subject: dict | None,
        inputs: list[str],
    ) -> dict:
        """Upload the inputs to the remote scope, start the run there, and store the local record
        that points at it. Returns that record."""
        for key in inputs:
            try:
                data = await storage.get_bytes(scope, key)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail=f"no input file at {key!r}") from exc
            await self._call(
                "PUT",
                f"{self._scope_path()}/blobs/{quote(key, safe='/')}",
                what=f"uploading {key}",
                content=data,
                headers={"Content-Type": "application/octet-stream"},
            )
        body: dict[str, Any] = {"provider": provider_id, "parameters": parameters, "inputs": inputs}
        if kind is not None:
            body["kind"] = kind
        if subject is not None:
            body["subject"] = subject
        r = await self._call("POST", f"{self._scope_path()}/simulations", what="starting the run", json=body)
        record = self._mirror(None, _run_of(self, r))
        await write_run(storage, scope, record)
        return {**record, "cancel_requested": False}

    def _mirror(self, local: dict | None, remote_run: dict) -> dict:
        """The local record of a remote run: the remote's fields, and where it lives."""
        record = {k: v for k, v in remote_run.items() if k != "cancel_requested"}
        record["remote"] = {"url": self.url, "scope": self.remote.scope, "run_id": record["run_id"]}
        try:
            return parse_run_record(json.dumps(record))
        except RunRecordError as exc:
            raise self._fail("reading the run", exc) from exc

    async def view(self, storage, scope: Scope, record: dict, *, strict: bool) -> dict:
        """``record`` (a relayed run) as a client sees it, brought up to date from the remote and
        stored again when it changed. A finished run is answered from the local copy. ``strict``: an
        unreachable remote is a 502 (one run asked for); else the stored record is returned with
        ``remote_error`` saying why it is stale (a listing)."""
        if record.get("status") in TERMINAL_STATUSES:
            return {**record, "cancel_requested": False}
        remote = record["remote"]
        try:
            r = await self._call(
                "GET",
                f"/api/scopes/{quote(remote['scope'], safe='')}/simulations/{remote['run_id']}",
                what="reading the run",
                ok=(200,),
            )
            remote_run = _run_of(self, r)
        except HTTPException as exc:
            if strict:
                raise
            return {**record, "cancel_requested": False, "remote_error": str(exc.detail)}
        fresh = self._mirror(record, remote_run)
        if fresh != record:
            await write_run(storage, scope, fresh)
        return {**fresh, "cancel_requested": bool(remote_run.get("cancel_requested"))}

    async def cancel(self, storage, scope: Scope, record: dict) -> dict:
        """Ask the remote to stop the run: ``{"run", "cancelled"}`` as its cancel route answers."""
        remote = record["remote"]
        r = await self._call(
            "POST",
            f"/api/scopes/{quote(remote['scope'], safe='')}/simulations/{remote['run_id']}/cancel",
            what="cancelling the run",
            ok=(200,),
        )
        try:
            answer = r.json()
        except ValueError as exc:
            raise self._fail("cancelling the run", f"not JSON ({exc})") from exc
        remote_run = answer.get("run") if isinstance(answer, dict) else None
        if not isinstance(remote_run, dict) or "run_id" not in remote_run:
            raise self._fail("cancelling the run", "the answer has no run")
        fresh = self._mirror(record, remote_run)
        if fresh != record:
            await write_run(storage, scope, fresh)
        return {"run": {**fresh, "cancel_requested": bool(remote_run.get("cancel_requested"))}, "cancelled": bool(answer.get("cancelled"))}

    # -- artefacts -----------------------------------------------------------------------------

    async def proxy_blob(self, record: dict, key: str, request) -> StreamingResponse:
        """The remote's bytes of ``key`` (an artefact of a relayed run), as it answers: its status
        (206 / 416 for a range), ``Content-Range``, ``Content-Encoding`` and body, streamed through
        undecoded. The range arrives as the header or as ``range_start`` / ``range_end``; both are
        passed on."""
        remote = record["remote"]
        path = f"/api/scopes/{quote(remote['scope'], safe='')}/blobs/{quote(key.lstrip('/'), safe='/')}"
        headers: dict[str, str] = {}
        if request.headers.get("range"):
            headers["Range"] = request.headers["range"]
        client = self._client()
        try:
            req = client.build_request("GET", path, params=dict(request.query_params), headers=headers)
            upstream = await client.send(req, stream=True)
        except httpx.HTTPError as exc:
            await client.aclose()
            raise self._fail(f"fetching {key}", f"{type(exc).__name__}: {exc}") from exc

        async def close() -> None:
            await upstream.aclose()
            await client.aclose()

        out = {h: upstream.headers[h] for h in _PASS_HEADERS if h in upstream.headers and h != "content-type"}
        return StreamingResponse(
            upstream.aiter_raw(),
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/octet-stream"),
            headers=out,
            background=BackgroundTask(close),
        )

def _detail(r: httpx.Response) -> str:
    try:
        body = r.json()
        if isinstance(body, dict) and body.get("detail") is not None:
            return str(body["detail"])
    except ValueError:
        pass
    return r.text[:300]


def _run_of(relay: SimulationRelay, r: httpx.Response) -> dict:
    try:
        run = r.json().get("run")
    except (ValueError, AttributeError) as exc:
        raise relay._fail("reading the run", f"not JSON ({exc})") from exc
    if not isinstance(run, dict) or not isinstance(run.get("run_id"), str):
        raise relay._fail("reading the run", "the answer has no run")
    return run
