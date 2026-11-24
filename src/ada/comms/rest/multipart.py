"""Multipart uploads a remote worker makes under a job grant, without holding a storage key.

The API starts the upload, hands out a URL per part, and completes or aborts it; the worker only
ever PUTs bytes to the URLs it was given (``routes/job_storage.py``). Two ways to do that:

:class:`S3Multipart`
    The store's own multipart upload. The API makes the calls obstore cannot sign
    (CreateMultipartUpload, ListParts, CompleteMultipartUpload, AbortMultipartUpload) itself, with
    URLs re-signed by ``presign.resign_with_params``, and gives the worker presigned UploadPart
    URLs on the PUBLIC endpoint: the bytes go straight to the store. Needs the store's secret.

:class:`StagedMultipart`
    Any store, through the API: each part is PUT to the API, kept under ``_derived/_grant_uploads/``
    in the job's scope, and on completion streamed into the object and removed. For a filesystem
    store (a dev stack, tests) and a store whose credentials come from the environment, where
    presigning with query parameters is not possible. The bytes pass through the API, so it is the
    fallback, not the path a deployment with an object store takes.

Both answer ``list_parts`` from what the store holds, which is how an interrupted upload resumes:
the parts already there are skipped, the rest get fresh URLs.
"""

from __future__ import annotations

import posixpath
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import AsyncIterator
from xml.sax.saxutils import escape

import httpx

from .scope import Scope

__all__ = [
    "STAGING_PREFIX",
    "MultipartError",
    "PartInfo",
    "S3Multipart",
    "StagedMultipart",
    "UploadNotFound",
    "multipart_for",
]

#: Where :class:`StagedMultipart` keeps parts. Under ``_derived/`` so it is hidden from listings
#: and removed by "clear temp files" if an upload is abandoned.
STAGING_PREFIX = "_derived/_grant_uploads/"

_UPLOAD_ID = re.compile(r"^[A-Za-z0-9._~+/=-]{1,1024}$")


class MultipartError(RuntimeError):
    """The store refused a multipart call."""


class UploadNotFound(MultipartError):
    """No such upload for this key (finished, aborted, or never started)."""


@dataclass(frozen=True)
class PartInfo:
    part_number: int
    etag: str
    size: int

    def to_dict(self) -> dict:
        return {"part_number": self.part_number, "etag": self.etag, "size": self.size}


def check_upload_id(upload_id: object) -> str:
    if not isinstance(upload_id, str) or not _UPLOAD_ID.match(upload_id):
        raise UploadNotFound("not an upload id")
    return upload_id


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_text(root: ET.Element, name: str) -> str | None:
    for elem in root.iter():
        if _local(elem.tag) == name:
            return elem.text or ""
    return None


class S3Multipart:
    """The store's multipart upload (see the module docstring). ``transport`` is for tests."""

    kind = "s3"
    proxied = False

    def __init__(self, storage, *, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 60.0) -> None:
        self._storage = storage
        self._transport = transport
        self._timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=self._timeout)

    async def _call(self, scope: Scope, key: str, method: str, params: dict, *, content: bytes | None = None):
        url = await self._storage.sign_request(scope, key, method, params, internal=True)
        async with self._client() as client:
            resp = await client.request(method, url, content=content)
        body = resp.text
        if resp.status_code == 404 or "<Code>NoSuchUpload</Code>" in body:
            raise UploadNotFound("no such upload for this key")
        if resp.status_code >= 300 or "<Error>" in body:
            # CompleteMultipartUpload can answer 200 with an error document, so the body is read
            # whatever the status.
            code = None
            try:
                code = _find_text(ET.fromstring(body), "Code") if body.strip() else None
            except ET.ParseError:
                pass
            raise MultipartError(f"the store refused {method} ({resp.status_code}{', ' + code if code else ''})")
        return body

    async def create(self, scope: Scope, key: str) -> str:
        body = await self._call(scope, key, "POST", {"uploads": ""})
        upload_id = _find_text(ET.fromstring(body), "UploadId")
        if not upload_id:
            raise MultipartError("the store started an upload but named no upload id")
        return upload_id

    async def part_url(self, scope: Scope, key: str, upload_id: str, part_number: int, ttl_s: int) -> str:
        return await self._storage.sign_request(
            scope,
            key,
            "PUT",
            {"partNumber": str(part_number), "uploadId": upload_id},
            expires_in_seconds=ttl_s,
        )

    async def list_parts(self, scope: Scope, key: str, upload_id: str) -> list[PartInfo]:
        parts: list[PartInfo] = []
        marker = ""
        for _ in range(100):  # 1000 parts a page, S3 allows 10 000
            params = {"uploadId": upload_id, "max-parts": "1000"}
            if marker:
                params["part-number-marker"] = marker
            root = ET.fromstring(await self._call(scope, key, "GET", params))
            for elem in root.iter():
                if _local(elem.tag) != "Part":
                    continue
                fields = {_local(c.tag): (c.text or "") for c in elem}
                parts.append(
                    PartInfo(
                        part_number=int(fields.get("PartNumber") or 0),
                        etag=fields.get("ETag") or "",
                        size=int(fields.get("Size") or 0),
                    )
                )
            if (_find_text(root, "IsTruncated") or "").lower() != "true":
                break
            marker = _find_text(root, "NextPartNumberMarker") or ""
            if not marker:
                break
        return sorted(parts, key=lambda p: p.part_number)

    async def complete(self, scope: Scope, key: str, upload_id: str, parts: list[PartInfo]) -> None:
        xml = "".join(
            f"<Part><PartNumber>{p.part_number}</PartNumber><ETag>{escape(p.etag)}</ETag></Part>" for p in parts
        )
        body = f"<CompleteMultipartUpload>{xml}</CompleteMultipartUpload>".encode("utf-8")
        await self._call(scope, key, "POST", {"uploadId": upload_id}, content=body)

    async def abort(self, scope: Scope, key: str, upload_id: str) -> None:
        try:
            await self._call(scope, key, "DELETE", {"uploadId": upload_id})
        except UploadNotFound:
            pass


class StagedMultipart:
    """Parts staged in the store through the API (see the module docstring)."""

    kind = "staged"
    proxied = True

    def __init__(self, storage) -> None:
        self._storage = storage

    @staticmethod
    def _dir(upload_id: str) -> str:
        return f"{STAGING_PREFIX}{upload_id}/"

    @classmethod
    def _part_key(cls, upload_id: str, part_number: int) -> str:
        return f"{cls._dir(upload_id)}{part_number:05d}"

    async def _check(self, scope: Scope, key: str, upload_id: str) -> None:
        """The upload exists and is for ``key``: an upload id is only good for its own key."""
        import json

        check_upload_id(upload_id)
        if "/" in upload_id or upload_id in (".", ".."):
            raise UploadNotFound("not an upload id")
        try:
            meta = json.loads(await self._storage.get_bytes(scope, f"{self._dir(upload_id)}upload.json"))
        except (FileNotFoundError, KeyError, ValueError):
            raise UploadNotFound("no such upload") from None
        except Exception as exc:  # noqa: BLE001 - obstore's not-found
            if "not found" in str(exc).lower():
                raise UploadNotFound("no such upload") from None
            raise
        if meta.get("key") != key:
            raise UploadNotFound("no such upload for this key")

    async def create(self, scope: Scope, key: str) -> str:
        import json

        upload_id = uuid.uuid4().hex
        await self._storage.put_bytes(scope, f"{self._dir(upload_id)}upload.json", json.dumps({"key": key}).encode())
        return upload_id

    async def put_part(
        self, scope: Scope, key: str, upload_id: str, part_number: int, chunks: AsyncIterator[bytes]
    ) -> PartInfo:
        await self._check(scope, key, upload_id)
        part_key = self._part_key(upload_id, part_number)
        await self._storage.put_stream(scope, part_key, chunks)
        meta = await self._storage.head(scope, part_key)
        if meta is None:
            raise MultipartError("the part did not land")
        return PartInfo(part_number=part_number, etag=self._etag(meta, part_number), size=int(meta["size"]))

    @staticmethod
    def _etag(meta: dict, part_number: int) -> str:
        tag = meta.get("e_tag") or f"{meta.get('size')}-{meta.get('last_modified')}-{part_number}"
        return tag if tag.startswith('"') else f'"{tag}"'

    async def list_parts(self, scope: Scope, key: str, upload_id: str) -> list[PartInfo]:
        await self._check(scope, key, upload_id)
        out = []
        for entry in await self._storage.list_prefix(scope, self._dir(upload_id)):
            name = posixpath.basename(entry.key)
            if not name.isdigit():
                continue
            meta = await self._storage.head(scope, entry.key)
            if meta is not None:
                out.append(PartInfo(part_number=int(name), etag=self._etag(meta, int(name)), size=int(meta["size"])))
        return sorted(out, key=lambda p: p.part_number)

    async def complete(self, scope: Scope, key: str, upload_id: str, parts: list[PartInfo]) -> None:
        await self._check(scope, key, upload_id)
        storage = self._storage

        async def chunks() -> AsyncIterator[bytes]:
            for part in parts:
                async for chunk in storage.iter_raw(scope, self._part_key(upload_id, part.part_number)):
                    yield chunk

        await storage.put_stream(scope, key, chunks())
        await self._remove(scope, upload_id)

    async def abort(self, scope: Scope, key: str, upload_id: str) -> None:
        try:
            await self._check(scope, key, upload_id)
        except UploadNotFound:
            return  # already gone, or not this key's: nothing of this upload to remove
        await self._remove(scope, upload_id)

    async def _remove(self, scope: Scope, upload_id: str) -> None:
        keys = [e.key for e in await self._storage.list_prefix(scope, self._dir(upload_id))]
        if keys:
            await self._storage.delete_many(scope, keys)


def multipart_for(storage, *, transport: httpx.AsyncBaseTransport | None = None):
    """The multipart upload this store can do: the store's own when the API can sign for it,
    else parts staged through the API."""
    if getattr(storage, "can_sign_requests", False):
        return S3Multipart(storage, transport=transport)
    return StagedMultipart(storage)
