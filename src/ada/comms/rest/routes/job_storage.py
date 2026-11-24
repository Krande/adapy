"""Job-scoped storage for remote workers: ``/api/jobs/{job_id}/storage/...``, job grant only.

A remote worker holds no storage keys. It asks for a job grant over NATS when it picks a job up
(``job_grants.py``) and sends it here as its bearer. Nothing else is accepted: not a user's token,
not a CLI token, not a grant for another job. These routes are mounted outside the user-auth router
for that reason, and answer 404 unless the deployment turned job grants on
(``ADA_VIEWER_WORKER_GRANT_SECRET``).

Every call recomputes what the job may touch from the job and its run record
(``job_grants.job_access``) -- the token only says which job and which worker:

* ``GET  object?key=``           a small object's bytes (gzip at rest undone), 413 above the limit
* ``PUT  object?key=``           a small object (``content_encoding=gzip`` to store it gzipped)
* ``GET  download-url?key=``     a presigned GET on the store's public endpoint (or this API's ``blob``)
* ``GET  blob?key=``             the stored bytes through the API, for a store that cannot presign
* ``GET  list?prefix=``          the keys under a prefix the job may read or write
* ``POST upload``                ``{key, size}``: one presigned PUT up to the part size; above it a
                                 multipart upload, with ``upload_id`` and the first part URLs
* ``POST upload/parts``          ``{key, upload_id, part_numbers?}``: the parts the store holds
                                 (resume) and fresh URLs for the ones asked for
* ``POST upload/complete``       ``{key, upload_id, parts?}``
* ``POST upload/abort``          ``{key, upload_id}``
* ``PUT  upload/single|part``    the bytes themselves, when the store cannot presign (staged)

Reads resolve provider data the way the blob routes do (``routes/storage._read_scope``). Grants,
writes and presigned downloads leave an audit row with the worker's name (``worker:<name>``).
"""

from __future__ import annotations

import math
from typing import AsyncIterator
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from ada.config import logger

from ..job_grants import (
    GrantRefused,
    GrantService,
    GrantTokenError,
    JobAccess,
    safe_key,
)
from ..multipart import (
    MultipartError,
    PartInfo,
    StagedMultipart,
    UploadNotFound,
    check_upload_id,
    multipart_for,
)
from ..queue import Job

router = APIRouter(prefix="/api/jobs/{job_id}/storage")

#: How many part URLs one answer carries; the worker asks for the next ones as it goes, which
#: also keeps each URL fresh for a long upload.
PART_URL_WINDOW = 64


def _service(request: Request) -> GrantService:
    service = getattr(request.app.state, "job_grants", None)
    if service is None:
        raise HTTPException(status_code=404, detail="job grants are not enabled on this server")
    return service


def _bearer(request: Request) -> str:
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="a job grant is required", headers={"WWW-Authenticate": "Bearer"})
    return token.strip()


class _Granted:
    """One call's grant: the service, the job, what it may touch and who is asking."""

    def __init__(self, service: GrantService, job: Job, access: JobAccess, worker: str) -> None:
        self.service, self.job, self.access, self.worker = service, job, access, worker

    @property
    def scope(self):
        return self.access.scope

    @property
    def storage(self):
        return self.service.storage

    def readable(self, raw: object) -> str:
        key = _key(raw)
        if not self.access.can_read(key):
            raise HTTPException(status_code=403, detail=f"{key!r} is not one this job may read")
        return key

    def writable(self, raw: object) -> str:
        key = _key(raw)
        if not self.access.can_write(key):
            raise HTTPException(status_code=403, detail=f"{key!r} is not one this job may write")
        return key

    async def audit(self, action: str, **kw) -> None:
        await self.service.audit(action, self.job, self.worker, **kw)


def _key(raw: object) -> str:
    try:
        return safe_key(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _granted(request: Request, job_id: str) -> _Granted:
    service = _service(request)
    try:
        claims, access, job = await service.authorise(_bearer(request), job_id)
    except GrantTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc
    except GrantRefused as exc:
        raise HTTPException(status_code=exc.status, detail={"error": exc.code, "detail": exc.detail}) from exc
    return _Granted(service, job, access, claims.worker)


async def _read_scope(request: Request, granted: _Granted, key: str):
    """Where a read of ``key`` is served from: the provider store for granted provider data it
    holds, else the job's scope -- the blob routes' rule (``routes/storage._read_scope``)."""
    ctx = getattr(request.app.state, "rest", None)
    if ctx is None:
        return granted.scope
    from .storage import _read_scope as blob_read_scope

    return await blob_read_scope(ctx, request, granted.scope, key)


async def _list_scope(request: Request, granted: _Granted, prefix: str):
    """Where a listing of ``prefix`` is served from: the provider store when the prefix is a
    provider dataset the scope is granted and the store holds (``provider_store.resolve_collection``,
    the rule a worker's ``ProviderReads`` applies), else the job's scope."""
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None or granted.scope.kind == "providers":
        return granted.scope
    from types import SimpleNamespace

    from .provider_store import dataset_collection_of_key, resolve_collection

    collection = dataset_collection_of_key(prefix)
    if collection is None:
        return granted.scope
    try:
        return await resolve_collection(
            SimpleNamespace(storage=granted.storage), pool, granted.scope, collection, "geometry"
        )
    except Exception as exc:  # noqa: BLE001 - an unanswerable grant lists the scope's own copy, never more
        logger.warning("job storage: could not resolve %r for %s: %s", collection, granted.scope.prefix(), exc)
        return granted.scope


def _api_url(job_id: str, path: str, **params) -> str:
    return f"/api/jobs/{job_id}/storage/{path}?{urlencode(params)}"


async def _bounded(request: Request, limit: int) -> AsyncIterator[bytes]:
    """The request body, refused once it passes ``limit`` bytes (the store discards the partial
    upload when the iterator raises)."""
    seen = 0
    async for chunk in request.stream():
        seen += len(chunk)
        if seen > limit:
            raise HTTPException(status_code=413, detail=f"the body is larger than {limit} bytes")
        if chunk:
            yield chunk


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="the body is not JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="the body must be a JSON object")
    return body


# ── reads ────────────────────────────────────────────────────────────────────────────────────────


@router.get("/object")
async def get_object(job_id: str, key: str, request: Request) -> Response:
    granted = await _granted(request, job_id)
    key = granted.readable(key)
    where = await _read_scope(request, granted, key)
    meta = await granted.storage.head(where, key)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"no object at {key!r}")
    limit = granted.service.cfg.small_object_max
    if int(meta["size"]) > limit:
        raise HTTPException(status_code=413, detail=f"{key!r} is larger than {limit} bytes; use download-url")
    try:
        data = await granted.storage.get_bytes(where, key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"no object at {key!r}") from None
    return Response(content=data, media_type="application/octet-stream")


@router.get("/download-url")
async def download_url(job_id: str, key: str, request: Request) -> JSONResponse:
    granted = await _granted(request, job_id)
    key = granted.readable(key)
    where = await _read_scope(request, granted, key)
    meta = await granted.storage.head(where, key)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"no object at {key!r}")
    ttl = granted.service.cfg.ttl_s
    if granted.storage.supports_presigned_uploads:
        url = await granted.storage.presigned_get_url(where, key, expires_in_seconds=ttl)
        via = "store"
    else:
        url, via = _api_url(job_id, "blob", key=key), "api"
    await granted.audit("job_storage_download", key=key, status="ok")
    return JSONResponse({"key": key, "url": url, "via": via, "size": int(meta["size"]), "expires_in": ttl})


@router.get("/blob")
async def get_blob(job_id: str, key: str, request: Request) -> StreamingResponse:
    """The stored bytes, as stored (gzip at rest is not undone: the reader sniffs, as it does a
    presigned GET's body)."""
    granted = await _granted(request, job_id)
    key = granted.readable(key)
    where = await _read_scope(request, granted, key)
    stream = granted.storage.iter_raw(where, key)
    try:
        first = await stream.__anext__()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"no object at {key!r}") from None
    except StopAsyncIteration:
        first = b""

    async def body() -> AsyncIterator[bytes]:
        if first:
            yield first
        async for chunk in stream:
            yield chunk

    return StreamingResponse(body(), media_type="application/octet-stream")


@router.get("/list")
async def list_keys(job_id: str, prefix: str, request: Request) -> JSONResponse:
    granted = await _granted(request, job_id)
    try:
        prefix = safe_key(prefix, prefix=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not granted.access.can_list(prefix):
        raise HTTPException(status_code=403, detail=f"{prefix!r} is not a prefix this job may list")
    where = granted.scope
    if not granted.access.can_write(prefix):
        # A read prefix may be provider data: listed where the scope reads it.
        where = await _list_scope(request, granted, prefix)
    entries = await granted.storage.list_prefix(where, prefix)
    keys = [{"key": e.key, "size": e.size} for e in entries if e.key.startswith(prefix)]
    return JSONResponse({"prefix": prefix, "keys": keys})


# ── small writes ─────────────────────────────────────────────────────────────────────────────────


@router.put("/object")
async def put_object(
    job_id: str,
    key: str,
    request: Request,
    content_encoding: str | None = None,
    pre_compressed: bool = False,
) -> JSONResponse:
    granted = await _granted(request, job_id)
    key = granted.writable(key)
    if content_encoding not in (None, "", "gzip"):
        raise HTTPException(status_code=400, detail="content_encoding must be gzip or absent")
    limit = granted.service.cfg.small_object_max
    data = b"".join([chunk async for chunk in _bounded(request, limit)])
    await granted.storage.put_bytes(
        granted.scope, key, data, content_encoding=content_encoding or None, pre_compressed=pre_compressed
    )
    await granted.audit("job_storage_put", key=key, status="ok", write_bytes=len(data))
    return JSONResponse({"key": key, "size": len(data)})


# ── uploads ──────────────────────────────────────────────────────────────────────────────────────


def _backend(granted: _Granted):
    return multipart_for(granted.storage, transport=getattr(granted.service, "http_transport", None))


async def _part_urls(granted: _Granted, backend, key: str, upload_id: str, numbers: list[int]) -> list[dict]:
    ttl = granted.service.cfg.ttl_s
    out = []
    for n in numbers:
        if backend.proxied:
            url = _api_url(granted.job.job_id, "upload/part", key=key, upload_id=upload_id, part_number=n)
            out.append({"part_number": n, "url": url, "via": "api"})
        else:
            out.append(
                {"part_number": n, "url": await backend.part_url(granted.scope, key, upload_id, n, ttl), "via": "store"}
            )
    return out


def _part_count(size: int, part_size: int) -> int:
    return max(1, math.ceil(size / part_size))


@router.post("/upload")
async def start_upload(job_id: str, request: Request) -> JSONResponse:
    """``{key, size}`` -> ``{"mode": "single", "url", "via"}`` for a file up to the part size, else
    ``{"mode": "multipart", "upload_id", "part_size", "part_count", "parts": [{part_number, url}]}``
    with the first :data:`PART_URL_WINDOW` part URLs."""
    granted = await _granted(request, job_id)
    body = await _json(request)
    key = granted.writable(body.get("key"))
    size = body.get("size")
    if not isinstance(size, int) or size < 0:
        raise HTTPException(status_code=400, detail="'size' (the file's size in bytes) is required")
    cfg = granted.service.cfg
    if size > cfg.max_size:
        raise HTTPException(status_code=413, detail=f"{size} bytes is more than a grant may upload ({cfg.max_size})")
    if size <= cfg.part_size:
        if granted.storage.supports_presigned_uploads:
            url = await granted.storage.presigned_put_url(granted.scope, key, expires_in_seconds=cfg.ttl_s)
            via = "store"
        else:
            url, via = _api_url(job_id, "upload/single", key=key), "api"
        await granted.audit("job_storage_upload", key=key, status="single", write_bytes=size)
        return JSONResponse({"mode": "single", "key": key, "url": url, "method": "PUT", "via": via})

    backend = _backend(granted)
    try:
        upload_id = await backend.create(granted.scope, key)
    except MultipartError as exc:
        logger.warning("job storage: could not start an upload of %s for job %s: %s", key, job_id, exc)
        raise HTTPException(status_code=502, detail=f"the store would not start the upload: {exc}") from exc
    count = _part_count(size, cfg.part_size)
    parts = await _part_urls(granted, backend, key, upload_id, list(range(1, min(count, PART_URL_WINDOW) + 1)))
    await granted.audit("job_storage_upload", key=key, status="multipart", write_bytes=size)
    return JSONResponse(
        {
            "mode": "multipart",
            "key": key,
            "upload_id": upload_id,
            "part_size": cfg.part_size,
            "part_count": count,
            "parts": parts,
            "method": "PUT",
        }
    )


def _upload_id(body: dict) -> str:
    try:
        return check_upload_id(body.get("upload_id"))
    except UploadNotFound:
        raise HTTPException(status_code=400, detail="'upload_id' is required") from None


async def _listed(granted: _Granted, backend, key: str, upload_id: str) -> list[PartInfo]:
    try:
        return await backend.list_parts(granted.scope, key, upload_id)
    except UploadNotFound:
        raise HTTPException(status_code=404, detail="no such upload for this key") from None
    except MultipartError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/upload/parts")
async def upload_parts(job_id: str, request: Request) -> JSONResponse:
    """``{key, upload_id, part_numbers?}`` -> ``{"uploaded": [{part_number, etag, size}], "parts":
    [{part_number, url}]}``: what the store already holds of the upload (so an interrupted one
    resumes where it stopped) and fresh URLs for the part numbers asked for."""
    granted = await _granted(request, job_id)
    body = await _json(request)
    key = granted.writable(body.get("key"))
    upload_id = _upload_id(body)
    numbers = body.get("part_numbers") or []
    if not isinstance(numbers, list) or not all(isinstance(n, int) and 1 <= n <= 10_000 for n in numbers):
        raise HTTPException(status_code=400, detail="'part_numbers' must be a list of part numbers (1..10000)")
    if len(numbers) > PART_URL_WINDOW:
        raise HTTPException(status_code=400, detail=f"at most {PART_URL_WINDOW} part URLs per call")
    backend = _backend(granted)
    uploaded = await _listed(granted, backend, key, upload_id)
    parts = await _part_urls(granted, backend, key, upload_id, sorted(set(numbers)))
    return JSONResponse({"upload_id": upload_id, "uploaded": [p.to_dict() for p in uploaded], "parts": parts})


def _norm_etag(tag: object) -> str:
    return str(tag or "").strip().strip('"')


@router.post("/upload/complete")
async def complete_upload(job_id: str, request: Request) -> JSONResponse:
    """``{key, upload_id, parts?: [{part_number, etag}]}``. The parts are checked against what the
    store holds (every one present, with that ETag, numbered 1..n); without ``parts`` every part the
    store holds is used. The total is checked against the grant's size limit."""
    granted = await _granted(request, job_id)
    body = await _json(request)
    key = granted.writable(body.get("key"))
    upload_id = _upload_id(body)
    backend = _backend(granted)
    held = {p.part_number: p for p in await _listed(granted, backend, key, upload_id)}
    wanted = body.get("parts")
    if wanted is None:
        chosen = [held[n] for n in sorted(held)]
    else:
        if not isinstance(wanted, list) or not wanted:
            raise HTTPException(status_code=400, detail="'parts' must be a non-empty list")
        chosen = []
        for item in wanted:
            n = item.get("part_number") if isinstance(item, dict) else None
            part = held.get(n) if isinstance(n, int) else None
            if part is None:
                raise HTTPException(status_code=409, detail=f"part {n} is not in the store; upload it again")
            if "etag" in item and _norm_etag(item["etag"]) != _norm_etag(part.etag):
                raise HTTPException(
                    status_code=409, detail=f"part {n} in the store is not the one sent; upload it again"
                )
            chosen.append(part)
        chosen.sort(key=lambda p: p.part_number)
    if not chosen or [p.part_number for p in chosen] != list(range(1, len(chosen) + 1)):
        raise HTTPException(status_code=409, detail="the parts must be numbered 1..n with none missing")
    total = sum(p.size for p in chosen)
    cfg = granted.service.cfg
    if total > cfg.max_size:
        await backend.abort(granted.scope, key, upload_id)
        raise HTTPException(status_code=413, detail=f"{total} bytes is more than a grant may upload ({cfg.max_size})")
    try:
        await backend.complete(granted.scope, key, upload_id, chosen)
    except UploadNotFound:
        raise HTTPException(status_code=404, detail="no such upload for this key") from None
    except MultipartError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    await granted.audit("job_storage_upload_complete", key=key, status="ok", write_bytes=total)
    return JSONResponse({"key": key, "size": total, "parts": len(chosen)})


@router.post("/upload/abort")
async def abort_upload(job_id: str, request: Request) -> JSONResponse:
    granted = await _granted(request, job_id)
    body = await _json(request)
    key = granted.writable(body.get("key"))
    upload_id = _upload_id(body)
    try:
        await _backend(granted).abort(granted.scope, key, upload_id)
    except MultipartError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    await granted.audit("job_storage_upload_abort", key=key, status="ok")
    return JSONResponse({"key": key, "aborted": True})


# ── the bytes, when the store cannot take them directly ──────────────────────────────────────────


@router.put("/upload/single")
async def put_single(job_id: str, key: str, request: Request) -> JSONResponse:
    granted = await _granted(request, job_id)
    key = granted.writable(key)
    limit = granted.service.cfg.part_size
    size = 0

    async def counted() -> AsyncIterator[bytes]:
        nonlocal size
        async for chunk in _bounded(request, limit):
            size += len(chunk)
            yield chunk

    await granted.storage.put_stream(granted.scope, key, counted())
    return JSONResponse({"key": key, "size": size})


@router.put("/upload/part")
async def put_part(job_id: str, key: str, upload_id: str, part_number: int, request: Request) -> JSONResponse:
    granted = await _granted(request, job_id)
    key = granted.writable(key)
    if not 1 <= part_number <= 10_000:
        raise HTTPException(status_code=400, detail="part_number must be 1..10000")
    backend = _backend(granted)
    if not isinstance(backend, StagedMultipart):
        raise HTTPException(status_code=409, detail="this store takes parts directly; PUT to the part URL")
    try:
        part = await backend.put_part(
            granted.scope, key, upload_id, part_number, _bounded(request, granted.service.cfg.part_size)
        )
    except UploadNotFound:
        raise HTTPException(status_code=404, detail="no such upload for this key") from None
    return JSONResponse(part.to_dict(), headers={"ETag": part.etag})
