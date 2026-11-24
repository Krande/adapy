"""``RemoteGrantStorage``: one job's storage through the API, under a job grant.

The same six methods as the worker's ``_SyncStorageFacade`` (``list_keys``, ``get_bytes``,
``fetch_to_path``, ``put_bytes``, ``put_path``, ``provider_denial``), so ``execute_run`` and a
provider cannot tell which one they were handed. Synchronous and blocking, as that facade is: it
is called from the thread a provider runs on.

Small objects (a run record, the cancel marker, a log) go through the API in one call. Large ones
move between this machine and the store directly: a presigned GET, a presigned PUT up to the part
size, and above it a multipart upload the API starts and completes, the parts PUT here with a few
retries each. After a failure the upload asks the API which parts the store already holds and
sends only the rest.

The grant comes from ``token_provider(force=False)``; on a 401 the facade asks for a fresh one
(``force=True``) and tries once more. A grant is only ever sent to the API: presigned URLs carry
their own signature, and a store refuses a request that also has an Authorization header.
"""

from __future__ import annotations

import gzip
import pathlib
import shutil
import tempfile
import time
import zlib
from typing import Callable, Optional

import httpx

from ada.config import logger

__all__ = ["RemoteGrantStorage", "RemoteStorageError"]

_GZIP_MAGIC = b"\x1f\x8b"


class RemoteStorageError(RuntimeError):
    """The API or the store failed a call in a way retrying did not fix."""


TokenProvider = Callable[..., str]


class RemoteGrantStorage:
    """One job's storage through the API's ``/api/jobs/{job_id}/storage/...`` routes.

    ``client`` is an ``httpx.Client`` whose ``base_url`` is the API (made from ``api_url`` when not
    given; a test passes a FastAPI ``TestClient``). ``small_object_max`` is the bootstrap's
    ``grants.small_object_max``: above it, writes take the upload path.
    """

    _ATTEMPTS = 3
    _PART_ATTEMPTS = 4

    def __init__(
        self,
        job_id: str,
        token_provider: TokenProvider,
        *,
        api_url: str | None = None,
        client: httpx.Client | None = None,
        small_object_max: int = 8 * 1024 * 1024,
        timeout_s: float = 120.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if client is None:
            if not api_url:
                raise ValueError("RemoteGrantStorage needs api_url or client")
            client = httpx.Client(base_url=api_url.rstrip("/"), timeout=timeout_s, follow_redirects=False)
        self._client = client
        self._job_id = job_id
        self._token = token_provider
        self._small = small_object_max
        self._sleep = sleep
        self._base = f"/api/jobs/{job_id}/storage"

    @property
    def job_id(self) -> str:
        return self._job_id

    def close(self) -> None:
        self._client.close()

    # -- the API ----------------------------------------------------------------------------------

    def _is_api(self, url: str) -> bool:
        if url.startswith("/"):
            return True
        base = str(self._client.base_url).rstrip("/")
        return bool(base) and url.startswith(base + "/")

    def _api(self, method: str, path: str, *, params=None, json=None, content=None, retry: bool = True):
        """One API call with the grant; 401 renews the grant once, 5xx and transport errors are
        retried (``retry``), the rest is mapped to the exception a storage caller expects."""
        url = path if path.startswith("/") else f"{self._base}/{path}"
        attempts = self._ATTEMPTS if retry else 1
        renewed = False
        last: Exception | None = None
        attempt = 0
        while attempt < attempts:
            attempt += 1
            headers = {"Authorization": f"Bearer {self._token(force=False)}"}
            try:
                resp = self._client.request(method, url, params=params, json=json, content=content, headers=headers)
            except httpx.TransportError as exc:
                last = RemoteStorageError(f"the API is unreachable: {type(exc).__name__}")
                self._backoff(attempt, attempts)
                continue
            if resp.status_code == 401 and not renewed:
                renewed = True
                self._token(force=True)
                attempt -= 1  # a renewal is not a failed attempt
                continue
            if resp.status_code >= 500:
                last = RemoteStorageError(f"the API failed {method} {path} ({resp.status_code}): {_detail(resp)}")
                self._backoff(attempt, attempts)
                continue
            return resp
        raise last if last is not None else RemoteStorageError(f"{method} {path} failed")

    def _backoff(self, attempt: int, attempts: int) -> None:
        if attempt < attempts:
            self._sleep(min(2 ** (attempt - 1), 8))

    @staticmethod
    def _raise_for(resp: httpx.Response, key: str) -> None:
        if resp.status_code < 300:
            return
        detail = _detail(resp)
        if resp.status_code == 404:
            raise FileNotFoundError(f"object not found: {key}")
        if resp.status_code in (401, 403):
            raise PermissionError(f"{key!r}: {detail}")
        raise RemoteStorageError(f"{key!r}: the API answered {resp.status_code}: {detail}")

    # -- reads ------------------------------------------------------------------------------------

    def list_keys(self, prefix: str = "") -> list[str]:
        resp = self._api("GET", "list", params={"prefix": prefix})
        self._raise_for(resp, prefix)
        return [entry["key"] for entry in resp.json().get("keys") or []]

    def get_bytes(self, key: str) -> bytes:
        resp = self._api("GET", "object", params={"key": key})
        if resp.status_code == 413:
            # Larger than the API carries: straight from the store.
            with tempfile.TemporaryDirectory(prefix="ada-grant-get-") as tmp:
                dest = pathlib.Path(tmp) / "object"
                self.fetch_to_path(key, dest)
                return dest.read_bytes()
        self._raise_for(resp, key)
        return resp.content

    def fetch_to_path(self, key: str, dest):
        """Stream ``key`` to ``dest`` (gzip at rest undone, as ``Storage.stream_to_path`` does)."""
        resp = self._api("GET", "download-url", params={"key": key})
        self._raise_for(resp, key)
        url = resp.json()["url"]
        dest = pathlib.Path(dest)
        last: Exception | None = None
        for attempt in range(1, self._ATTEMPTS + 1):
            try:
                self._download(url, dest)
                return dest
            except FileNotFoundError:
                raise
            except (httpx.TransportError, RemoteStorageError) as exc:
                last = exc
                self._backoff(attempt, self._ATTEMPTS)
        raise RemoteStorageError(f"{key!r}: download failed: {last}")

    def _download(self, url: str, dest: pathlib.Path) -> None:
        headers = {"Authorization": f"Bearer {self._token(force=False)}"} if self._is_api(url) else {}
        with self._client.stream("GET", url, headers=headers) as resp:
            if resp.status_code == 404:
                raise FileNotFoundError(f"object not found: {dest.name}")
            if resp.status_code >= 300:
                raise RemoteStorageError(f"download answered {resp.status_code}")
            decomp = None
            first = True
            with open(dest, "wb") as fh:
                for chunk in resp.iter_raw():
                    if first and chunk:
                        first = False
                        if chunk[:2] == _GZIP_MAGIC:
                            decomp = zlib.decompressobj(zlib.MAX_WBITS | 16)
                    fh.write(decomp.decompress(chunk) if decomp is not None else chunk)
                if decomp is not None:
                    fh.write(decomp.flush())

    def provider_denial(self, key: str) -> Optional[str]:
        """Always None: the API resolves provider data for this job's reads, and a key the job may
        not read is refused with a PermissionError that says why."""
        return None

    # -- writes -----------------------------------------------------------------------------------

    def put_bytes(
        self, key: str, data: bytes, content_encoding: "str | None" = None, pre_compressed: bool = False
    ) -> None:
        if content_encoding not in (None, "gzip"):
            raise ValueError(f"unsupported content_encoding: {content_encoding!r}")
        data = bytes(data)
        if len(data) <= self._small:
            params = {"key": key}
            if content_encoding:
                params["content_encoding"] = content_encoding
            if pre_compressed:
                params["pre_compressed"] = "true"
            resp = self._api("PUT", "object", params=params, content=data)
            if resp.status_code != 413:
                self._raise_for(resp, key)
                return
        # Large: gzip here when asked (the store keeps the bytes; readers sniff the magic), then upload.
        if content_encoding == "gzip" and not pre_compressed:
            data = gzip.compress(data)
        with tempfile.TemporaryDirectory(prefix="ada-grant-put-") as tmp:
            path = pathlib.Path(tmp) / "object"
            path.write_bytes(data)
            self.upload_file(key, path)

    def put_path(self, key: str, src, content_encoding: "str | None" = None, pre_compressed: bool = False) -> dict:
        """Upload a file on local disk to ``key`` without reading it whole. Same return shape as
        ``Storage.put_path``: ``{"compress_ms", "upload_ms", "stored_bytes"}``."""
        if content_encoding not in (None, "gzip"):
            raise ValueError(f"unsupported content_encoding: {content_encoding!r}")
        src = pathlib.Path(src)
        if content_encoding == "gzip" and not pre_compressed:
            with tempfile.TemporaryDirectory(prefix="ada-grant-gz-") as tmp:
                gz = pathlib.Path(tmp) / (src.name + ".gz")
                t0 = time.monotonic()
                with open(src, "rb") as fin, gzip.open(gz, "wb", compresslevel=6) as fout:
                    shutil.copyfileobj(fin, fout, 1 << 20)
                compress_ms = round((time.monotonic() - t0) * 1000)
                out = self.upload_file(key, gz)
                return {**out, "compress_ms": compress_ms}
        return self.upload_file(key, src)

    def upload_file(self, key: str, src, *, resume_upload_id: str | None = None) -> dict:
        """Upload ``src`` to ``key``: one PUT up to the API's part size, a multipart upload above it.
        ``resume_upload_id`` continues a multipart upload of the same key and file that an earlier
        process started: the parts the store already holds are not sent again."""
        src = pathlib.Path(src)
        size = src.stat().st_size
        t0 = time.monotonic()
        if resume_upload_id:
            plan = {"mode": "multipart", "upload_id": resume_upload_id, "parts": []}
            plan["part_size"], plan["part_count"] = self._resume_shape(key, resume_upload_id, size)
        else:
            resp = self._api("POST", "upload", json={"key": key, "size": size}, retry=False)
            self._raise_for(resp, key)
            plan = resp.json()
        if plan["mode"] == "single":
            self._put_single(plan["url"], src, key)
        else:
            self._put_multipart(key, src, plan)
        return {"compress_ms": None, "upload_ms": round((time.monotonic() - t0) * 1000), "stored_bytes": size}

    def _resume_shape(self, key: str, upload_id: str, size: int) -> tuple[int, int]:
        resp = self._api("POST", "upload/parts", json={"key": key, "upload_id": upload_id})
        self._raise_for(resp, key)
        uploaded = resp.json().get("uploaded") or []
        part_size = max((p["size"] for p in uploaded if p["part_number"] == 1), default=0)
        if not part_size:
            raise RemoteStorageError(f"{key!r}: cannot resume upload: its first part is not in the store")
        return part_size, max(1, -(-size // part_size))

    def _send(self, url: str, content: bytes) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._token(force=False)}"} if self._is_api(url) else {}
        return self._client.put(url, content=content, headers=headers)

    def _put_single(self, url: str, src: pathlib.Path, key: str) -> None:
        last: Exception | None = None
        for attempt in range(1, self._ATTEMPTS + 1):
            try:
                resp = self._send(url, src.read_bytes())
            except httpx.TransportError as exc:
                last = exc
                self._backoff(attempt, self._ATTEMPTS)
                continue
            if resp.status_code < 300:
                return
            if resp.status_code in (401, 403) and attempt < self._ATTEMPTS:
                # An expired URL (or grant): ask for a new one.
                again = self._api("POST", "upload", json={"key": key, "size": src.stat().st_size}, retry=False)
                self._raise_for(again, key)
                url = again.json()["url"]
                continue
            last = RemoteStorageError(f"upload answered {resp.status_code}")
            self._backoff(attempt, self._ATTEMPTS)
        raise RemoteStorageError(f"{key!r}: upload failed: {last}")

    def _part_urls(self, key: str, upload_id: str, numbers: list[int]) -> tuple[dict[int, str], dict[int, dict]]:
        resp = self._api("POST", "upload/parts", json={"key": key, "upload_id": upload_id, "part_numbers": numbers})
        self._raise_for(resp, key)
        body = resp.json()
        urls = {p["part_number"]: p["url"] for p in body.get("parts") or []}
        uploaded = {p["part_number"]: p for p in body.get("uploaded") or []}
        return urls, uploaded

    def _put_multipart(self, key: str, src: pathlib.Path, plan: dict) -> None:
        upload_id = plan["upload_id"]
        part_size = int(plan["part_size"])
        count = int(plan["part_count"])
        urls = {p["part_number"]: p["url"] for p in plan.get("parts") or []}
        etags: dict[int, str] = {}
        window = max(len(urls), 1)
        try:
            # What the store already holds (a resumed upload): skipped.
            if not urls:
                _, held = self._part_urls(key, upload_id, [])
                etags.update({n: p["etag"] for n, p in held.items()})
                window = 64
            with open(src, "rb") as fh:
                n = 1
                while n <= count:
                    if n in etags:
                        n += 1
                        continue
                    if n not in urls:
                        wanted = [m for m in range(n, min(count, n + window - 1) + 1) if m not in etags]
                        fresh, _ = self._part_urls(key, upload_id, wanted)
                        urls.update(fresh)
                    fh.seek((n - 1) * part_size)
                    data = fh.read(part_size)
                    etags[n] = self._put_part(key, upload_id, n, data, urls)
                    n += 1
            parts = [{"part_number": n, "etag": etags[n]} for n in range(1, count + 1)]
            resp = self._api("POST", "upload/complete", json={"key": key, "upload_id": upload_id, "parts": parts})
            self._raise_for(resp, key)
        except BaseException:
            logger.warning("remote storage: upload of %s failed; aborting upload %s", key, upload_id)
            try:
                self._api("POST", "upload/abort", json={"key": key, "upload_id": upload_id}, retry=False)
            except Exception:  # noqa: BLE001 - the upload's own error is the one to raise
                pass
            raise

    def _put_part(self, key: str, upload_id: str, n: int, data: bytes, urls: dict[int, str]) -> str:
        """PUT one part, retried; a refused URL (expired) is renewed, and after a failure the store
        is asked whether the part landed anyway before it is sent again."""
        last: Exception | None = None
        for attempt in range(1, self._PART_ATTEMPTS + 1):
            try:
                resp = self._send(urls[n], data)
            except httpx.TransportError as exc:
                last = exc
                resp = None
            if resp is not None and resp.status_code < 300:
                etag = resp.headers.get("ETag") or ""
                if not etag:
                    # A store that hides the header from this client: the store's own list says.
                    _, held = self._part_urls(key, upload_id, [])
                    etag = (held.get(n) or {}).get("etag") or ""
                if etag:
                    return etag
                last = RemoteStorageError(f"part {n} has no ETag")
            elif resp is not None:
                last = RemoteStorageError(f"part {n} answered {resp.status_code}")
            if attempt == self._PART_ATTEMPTS:
                break
            self._backoff(attempt, self._PART_ATTEMPTS)
            fresh, held = self._part_urls(key, upload_id, [n])
            if n in held and held[n].get("size") == len(data):
                return held[n]["etag"]
            urls.update(fresh)
        raise RemoteStorageError(f"{key!r}: part {n} failed: {last}")


def _detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:300]
    detail = body.get("detail") if isinstance(body, dict) else body
    if isinstance(detail, dict):
        return f"{detail.get('error')}: {detail.get('detail')}"
    return str(detail)[:300]
