"""Storage abstraction backed by obstore.

obstore (the Python binding to the Rust object_store crate) gives us a
single async API across S3, GCS, Azure, and the local filesystem. The
viewer only needs list / get / stream so we keep the wrapper small.

Transparent gzip
================

Text-heavy formats (IFC, STEP, Genie XML, FEM input decks) compress
5–10× with gzip. Callers ask `put_bytes` to gzip on the way in via
`content_encoding="gzip"` and the storage layer:

* gzips the bytes, then writes them under the same logical key;
* tries to set the ``Content-Encoding`` attribute on the object so S3
  reports it on HEAD/GET (LocalStore raises NotImplementedError for
  attributes — we silently fall back to "no metadata, just bytes").

`get_bytes` always returns the decompressed payload. It detects gzip
content via the magic bytes 0x1F 0x8B at offset 0, which is bulletproof
across backends and survives metadata loss. `open_stream` peeks the
first chunk for the same magic, returns the raw (still-compressed)
stream plus a `content_encoding` hint, and lets the HTTP layer forward
``Content-Encoding: gzip`` so the browser auto-decompresses on download.
"""

from __future__ import annotations

import gzip
import logging
import os
import pathlib
import shutil
import subprocess
import time
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import AsyncIterator

import obstore as obs
from obstore.exceptions import NotFoundError as _ObstoreNotFound
from obstore.store import LocalStore, S3Store

from .config import Settings
from .scope import Scope

logger = logging.getLogger(__name__)

# gzip RFC 1952: every member starts with 0x1F 0x8B. Used as a portable
# encoding marker that doesn't depend on backend metadata support.
_GZIP_MAGIC = b"\x1f\x8b"

# pigz emits a *standard* gzip stream (Content-Encoding: gzip compatible) but
# multi-threaded, so it is a drop-in for the presigned-GET download path while
# running ~Ncore faster than single-threaded zlib. Resolved once at import.
_PIGZ = shutil.which("pigz")


def _cgroup_cpu_quota() -> int | None:
    """The pod's CPU limit from its cgroup (CFS quota), or None. k8s CPU *limits* are
    CFS quota, not cpuset, so ``sched_getaffinity`` would report the whole node and
    oversubscribe a capped pod. Handles cgroup v2 then v1. Kept local so this low-level
    storage module needn't import the heavy ``ada.visit`` stack (mirror of the copy in
    ``scene_from_step_stream``)."""
    try:  # cgroup v2
        with open("/sys/fs/cgroup/cpu.max") as f:
            quota, period = f.read().split()
        if quota != "max" and int(period) > 0:
            return max(1, round(int(quota) / int(period)))
    except (OSError, ValueError):
        pass
    try:  # cgroup v1
        with open("/sys/fs/cgroup/cpu/cpu.cfs_quota_us") as f:
            quota = int(f.read())
        with open("/sys/fs/cgroup/cpu/cpu.cfs_period_us") as f:
            period = int(f.read())
        if quota > 0 and period > 0:
            return max(1, round(quota / period))
    except (OSError, ValueError):
        pass
    return None


def _gzip_level() -> int:
    """gzip level for derived-output at-rest compression. Default 6, NOT the zlib/
    ``gzip.open`` default of 9: on large repetitive ASCII (a multi-GB OBJ/STEP export)
    level 9 costs ~8x the CPU of level 6 for a <1% smaller file — the level-9 pathology
    that made the large-assembly STEP->obj job spend ~900 s of its ~1070 s wall in gzip alone.
    ``ADA_DERIVED_GZIP_LEVEL`` overrides (clamped to 1..9)."""
    raw = os.environ.get("ADA_DERIVED_GZIP_LEVEL", "").strip()
    if raw:
        try:
            return min(9, max(1, int(raw)))
        except ValueError:
            pass
    return 6


def _gzip_threads() -> int:
    """Thread count for parallel gzip (pigz). ``ADA_DERIVED_GZIP_THREADS`` overrides;
    otherwise the pod's cgroup CPU limit, falling back to the schedulable CPU count.
    Bounded to >= 1."""
    raw = os.environ.get("ADA_DERIVED_GZIP_THREADS", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    n = _cgroup_cpu_quota()
    if n is None:
        try:
            n = len(os.sched_getaffinity(0))
        except (AttributeError, OSError):
            n = os.cpu_count() or 1
    return max(1, n)


def _gzip_file(src: pathlib.Path, dst: pathlib.Path, *, chunk_size: int = 1 << 20) -> None:
    """Stream-gzip ``src`` into ``dst``.

    Uses ``pigz`` (parallel gzip) when available — it emits a standard gzip stream so
    the stored object still serves ``Content-Encoding: gzip`` and the browser inflates
    transparently — at :func:`_gzip_level` across :func:`_gzip_threads` cores. Falls
    back to single-threaded zlib at the same level when pigz is missing or fails. Either
    way the payload streams a chunk at a time so neither the plain nor the gzipped bytes
    are held whole in RAM (the opposite of ``gzip.compress(data)``).
    """
    level = _gzip_level()
    if _PIGZ:
        threads = _gzip_threads()
        try:
            with open(dst, "wb") as fo:
                proc = subprocess.run(
                    [_PIGZ, f"-{level}", "-p", str(threads), "-c", str(src)],
                    stdout=fo,
                    stderr=subprocess.PIPE,
                )
            if proc.returncode == 0:
                return
            logger.warning(
                "pigz exited %s (%s); falling back to single-threaded gzip",
                proc.returncode,
                (proc.stderr or b"").decode("utf-8", "replace").strip()[:200],
            )
        except OSError as exc:  # pigz vanished between which() and exec, disk full, ...
            logger.warning("pigz invocation failed (%s); falling back to gzip", exc)
    with open(src, "rb") as fi, gzip.open(dst, "wb", compresslevel=level) as fo:
        while True:
            block = fi.read(chunk_size)
            if not block:
                break
            fo.write(block)


@dataclass(frozen=True)
class FileEntry:
    """Bucket-level file entry. ``key`` is scope-relative — the
    on-bucket prefix has been stripped, so callers see e.g.
    ``foo.ifc`` not ``users/abc/foo.ifc``.

    ``last_modified`` is an ISO-8601 string when the backend reports
    one (S3, Garage, MinIO all do), or None for backends that don't
    track it. The admin storage view sorts by it, so missing values
    sort consistently rather than crashing.
    """

    key: str
    size: int
    last_modified: str | None = None


@dataclass
class StreamResult:
    """Streaming read with an HTTP-style encoding hint.

    `content_encoding` is "gzip" if the stored bytes were compressed
    (and the caller is expected to forward it as a response header so
    the browser auto-decompresses), or None if the bytes are plain.
    """

    stream: AsyncIterator[bytes]
    content_encoding: str | None


class Storage:
    def __init__(self, store, prefix: str, presign_store=None) -> None:
        # `store` is whichever obstore backend implementation we built.
        # LocalStore and S3Store don't share a public Protocol but expose
        # the same get_async / list / stream surface.
        self._store = store
        # Used only by ``presigned_put_url``. Defaults to ``store`` so the
        # single-endpoint case stays a no-op. When the deployment hands
        # the browser a different (public HTTPS) hostname than the one
        # the API uses internally, this is a separate S3Store pointed at
        # the public endpoint so the URL it signs resolves + obeys
        # Mixed-Content rules. Same bucket + same credentials, just a
        # different endpoint — signatures stay valid because object_store
        # signs against the host header derived from the endpoint URL.
        self._presign_store = presign_store if presign_store is not None else store
        # Trim trailing slashes so callers can treat it as a directory.
        self._prefix = prefix.strip("/")

    @classmethod
    def from_settings(cls, settings: Settings) -> "Storage":
        if settings.storage_kind == "s3":
            assert settings.s3 is not None
            cfg = settings.s3
            kwargs: dict[str, object] = {
                "region": cfg.region,
                "virtual_hosted_style_request": cfg.virtual_hosted_style,
            }
            if cfg.endpoint:
                kwargs["endpoint"] = cfg.endpoint
                # Object_store / reqwest reject plain http:// schemes by
                # default. Cluster-local Garage / MinIO listen on http,
                # so we opt in when the endpoint clearly is http.
                if cfg.endpoint.lower().startswith("http://"):
                    kwargs["allow_http"] = True
            if cfg.access_key_id:
                kwargs["access_key_id"] = cfg.access_key_id
            if cfg.secret_access_key:
                kwargs["secret_access_key"] = cfg.secret_access_key
            store = S3Store(cfg.bucket, **kwargs)

            # Build a separate S3Store for presign-time URL minting only
            # when the operator has set a different public endpoint.
            # Skipping this when the two endpoints are equal avoids
            # holding two stores against the same target.
            presign_store = None
            if cfg.endpoint_public and cfg.endpoint_public != cfg.endpoint:
                presign_kwargs = dict(kwargs)
                presign_kwargs["endpoint"] = cfg.endpoint_public
                # Public endpoints should be HTTPS in practice; recompute
                # the allow_http flag against the public URL so we don't
                # leak the cluster-local relaxation to a public host (or
                # incorrectly require allow_http when the public URL is
                # https while the internal one is http).
                if cfg.endpoint_public.lower().startswith("http://"):
                    presign_kwargs["allow_http"] = True
                else:
                    presign_kwargs.pop("allow_http", None)
                presign_store = S3Store(cfg.bucket, **presign_kwargs)

            return cls(store, prefix=cfg.prefix, presign_store=presign_store)

        assert settings.local is not None
        return cls(LocalStore(settings.local.path), prefix=settings.local.prefix)

    def _scope_prefix(self, scope: Scope) -> str:
        """Combine the deployment-level base prefix with the scope's
        bucket sub-prefix. ``shared`` → ``<base>/shared``,
        ``users/<sub>`` → ``<base>/users/<sub>``, etc.
        """
        parts = [p for p in (self._prefix, scope.prefix()) if p]
        return "/".join(parts)

    def _full_key(self, scope: Scope, key: str) -> str:
        prefix = self._scope_prefix(scope)
        key = key.lstrip("/")
        if not prefix:
            return key
        return f"{prefix}/{key}"

    def _strip_scope_prefix(self, scope: Scope, full: str) -> str:
        prefix = self._scope_prefix(scope)
        if prefix and full.startswith(prefix + "/"):
            return full[len(prefix) + 1 :]
        return full

    def _entry(self, scope: Scope, meta) -> FileEntry:
        lm = meta.get("last_modified") if isinstance(meta, dict) else getattr(meta, "last_modified", None)
        lm_iso = lm.isoformat() if hasattr(lm, "isoformat") else (str(lm) if lm else None)
        return FileEntry(
            key=self._strip_scope_prefix(scope, meta["path"]),
            size=int(meta["size"]),
            last_modified=lm_iso,
        )

    async def list(self, scope: Scope, *, skip_prefixes: Sequence[str] = ()) -> list[FileEntry]:
        """Every object under ``scope``, or only those outside ``skip_prefixes``.

        ``skip_prefixes`` (e.g. ``("_derived/",)``) is a PERFORMANCE contract, not just a filter:
        those top-level namespaces are never enumerated. Filtering after a full listing costs the
        whole namespace — the audit corpus carries ~28k derived blobs against 163 real sources, so
        the file browser walked 28k keys to show 141 and took ~1.6 s, where a scope with the same
        file count but no audit history answered in 30 ms. Callers that need EVERY key (the
        orphan/derived cleanup in storage_ops) simply don't pass it, and are unaffected.
        """
        prefix = self._scope_prefix(scope)
        if not skip_prefixes:
            return await self._list_under(scope, prefix)

        # Walk one level with a delimiter so a skipped namespace costs a name in the response
        # rather than a full enumeration, then list only the prefixes we actually want.
        # is_hidden_key matches on the key's START, so top-level is the only level that can hide
        # anything — one delimited call is enough to find them all.
        skip = tuple(s if s.endswith("/") else f"{s}/" for s in skip_prefixes)
        root = await obs.list_with_delimiter_async(self._store, prefix=prefix or None)
        entries: list[FileEntry] = [self._entry(scope, m) for m in root["objects"]]
        for cp in root["common_prefixes"]:
            rel = self._strip_scope_prefix(scope, str(cp))
            rel = rel if rel.endswith("/") else f"{rel}/"
            if rel.startswith(skip):
                continue
            entries.extend(await self._list_under(scope, str(cp)))
        return entries

    async def _list_under(self, scope: Scope, prefix: str) -> list[FileEntry]:
        # obs.list returns a ListStream of pages; each page is a Sequence
        # of ObjectMeta dicts with keys "path", "size", "last_modified",
        # "e_tag" (last_modified is a datetime when present). The scope
        # prefix bounds the listing — a project list never sees user
        # files and vice versa.
        entries: list[FileEntry] = []
        stream = obs.list(self._store, prefix=prefix or None)
        async for page in stream:
            entries.extend(self._entry(scope, meta) for meta in page)
        return entries

    async def delete(self, scope: Scope, key: str) -> None:
        """Remove a single object. Raises FileNotFoundError when the
        backend reports the object doesn't exist; other backend errors
        propagate."""
        try:
            await self._store.delete_async(self._full_key(scope, key))
        except Exception:
            # obstore raises a generic error that may include "not found"
            # in the message; let callers translate to 404 if they want
            # a clean separation. We don't try to introspect — different
            # backends word it differently.
            raise

    async def get_bytes(self, scope: Scope, key: str) -> bytes:
        """Read an object and return its decompressed payload.

        Auto-decompresses if the stored bytes start with the gzip magic
        marker, regardless of whether the backend kept Content-Encoding
        metadata.
        """
        result = await self._store.get_async(self._full_key(scope, key))
        raw = bytes(await result.bytes_async())
        if raw[:2] == _GZIP_MAGIC:
            return gzip.decompress(raw)
        return raw

    async def put_bytes(
        self,
        scope: Scope,
        key: str,
        data: bytes,
        *,
        content_encoding: str | None = None,
        pre_compressed: bool = False,
    ) -> None:
        """Store an object, optionally gzipping it first.

        With ``content_encoding="gzip"`` the bytes are compressed and
        the Content-Encoding attribute is best-effort attached to the
        object. LocalStore doesn't support attributes; the gzip magic
        bytes still let the read path recognise compressed content.

        Set ``pre_compressed=True`` when ``data`` is already gzipped
        (e.g. produced by a streaming on-disk compression) — the
        Content-Encoding metadata still attaches but the body isn't
        re-gzipped.
        """
        if content_encoding is not None and content_encoding != "gzip":
            raise ValueError(f"unsupported content_encoding: {content_encoding!r}")

        full = self._full_key(scope, key)
        if content_encoding == "gzip":
            payload = data if pre_compressed else gzip.compress(data)
            try:
                await obs.put_async(
                    self._store,
                    full,
                    payload,
                    attributes={"Content-Encoding": "gzip"},
                )
                return
            except NotImplementedError:
                # LocalStore (filesystem) has no attribute slot. Fall
                # through and write the gzipped bytes — get_bytes /
                # open_stream sniff the magic header on read.
                await obs.put_async(self._store, full, payload)
                return

        await obs.put_async(self._store, full, data)

    async def put_path(
        self,
        scope: Scope,
        key: str,
        src_path: pathlib.Path,
        *,
        content_encoding: str | None = None,
        pre_compressed: bool = False,
    ) -> dict:
        """Stream a local file to object storage without buffering it whole.

        The mirror of :meth:`put_bytes` for output that already lives on
        disk (a conversion writer's tempfile). obstore reads the file and
        uploads via multipart, so peak RSS stays at the chunk size rather
        than the full payload — the whole point of this method is to NOT
        hand a multi-hundred-MB ``bytes`` object to the upload. Used by the
        worker for large derived outputs (STEP / IFC / GLB) that otherwise
        get materialised in RAM twice (child read-back + parent buffer).

        ``content_encoding="gzip"``:

        * ``pre_compressed=True`` — the file at ``src_path`` is already
          gzipped; we just attach the Content-Encoding attribute and stream
          it up as-is.
        * ``pre_compressed=False`` — we stream-gzip ``src_path`` into a
          sibling temp file first (chunked, never the whole payload in RAM),
          upload that, then delete it. Costs one extra bounded disk pass but
          keeps the ergonomics identical to ``put_bytes``.

        Returns a timing/size dict — ``{"compress_ms": int | None, "upload_ms": int,
        "stored_bytes": int}`` — so the worker can record the compression-vs-upload
        split per conversion in the audit log. ``compress_ms`` is None when no gzip
        pass ran (plain or ``pre_compressed``).
        """
        if content_encoding is not None and content_encoding != "gzip":
            raise ValueError(f"unsupported content_encoding: {content_encoding!r}")

        full = self._full_key(scope, key)
        src_path = pathlib.Path(src_path)

        if content_encoding == "gzip" and not pre_compressed:
            gz_path = src_path.with_name(src_path.name + ".gz")
            try:
                t0 = time.monotonic()
                _gzip_file(src_path, gz_path)
                compress_ms = round((time.monotonic() - t0) * 1000)
                stored_bytes = gz_path.stat().st_size
                t1 = time.monotonic()
                await self._put_file_stream(full, gz_path, content_encoding="gzip")
                upload_ms = round((time.monotonic() - t1) * 1000)
            finally:
                try:
                    gz_path.unlink()
                except OSError:
                    pass
            return {"compress_ms": compress_ms, "upload_ms": upload_ms, "stored_bytes": stored_bytes}

        t1 = time.monotonic()
        await self._put_file_stream(full, src_path, content_encoding=content_encoding)
        upload_ms = round((time.monotonic() - t1) * 1000)
        try:
            stored_bytes = src_path.stat().st_size
        except OSError:
            stored_bytes = 0
        return {"compress_ms": None, "upload_ms": upload_ms, "stored_bytes": stored_bytes}

    async def _put_file_stream(
        self,
        full: str,
        path: pathlib.Path,
        *,
        content_encoding: str | None = None,
    ) -> None:
        """Multipart-upload a local file at ``full`` (already scope-prefixed).

        Opens the file fresh for each attempt so the LocalStore
        attribute-unsupported retry doesn't replay a consumed cursor.
        """
        if content_encoding is not None:
            try:
                with open(path, "rb") as fh:
                    await obs.put_async(
                        self._store,
                        full,
                        fh,
                        use_multipart=True,
                        attributes={"Content-Encoding": content_encoding},
                    )
                return
            except NotImplementedError:
                # LocalStore has no attribute slot — fall through and write
                # the bytes plain. The gzip magic header still lets the read
                # path recognise compressed content.
                pass
        with open(path, "rb") as fh:
            await obs.put_async(self._store, full, fh, use_multipart=True)

    async def get_range(self, scope: Scope, key: str, start: int, length: int) -> bytes:
        """Read ``[start, start+length)`` raw bytes of an object — no gzip
        expansion. Only meaningful for objects stored *identity*-encoded;
        the FEA field blobs are (see worker.py / the artefact upload
        routes write ``.bin`` uncompressed), so the viewer can range-fetch
        a single step instead of the whole multi-step blob."""
        if length <= 0:
            return b""
        data = await obs.get_range_async(self._store, self._full_key(scope, key), start=start, length=length)
        return bytes(data)

    async def is_gzip_stored(self, scope: Scope, key: str) -> bool:
        """True if the object's stored bytes begin with the gzip magic —
        i.e. it was put with ``content_encoding="gzip"``. A byte range over
        such an object would be compressed bytes the browser can't map to
        logical offsets, so the Range route falls back to whole-object for
        these. Portable across backends (sniffs the magic, not metadata)."""
        try:
            head2 = await self.get_range(scope, key, 0, 2)
        except FileNotFoundError:
            raise
        except Exception:
            # A backend that can't range a tiny prefix — treat as
            # non-rangeable so the caller serves the whole object.
            return True
        return head2[:2] == _GZIP_MAGIC

    async def stream_to_path_raw(self, scope: Scope, key: str, dest_path: pathlib.Path) -> None:
        """Stream an object to a local file *without* decompressing
        gzipped bodies (in contrast to ``stream_to_path`` which
        transparently un-gzips on the fly).

        The compression sweep peeks at the stored magic bytes to
        decide whether to recompress; auto-decompressing would
        defeat that check. Used together with disk-side gzip so the
        viewer never has to hold a multi-hundred-MB payload in RAM.
        """
        result = await self._store.get_async(self._full_key(scope, key))
        with open(dest_path, "wb") as fh:
            async for chunk in result.stream():
                fh.write(bytes(chunk))

    async def rename(
        self,
        scope: Scope,
        src_key: str,
        dst_key: str,
        *,
        overwrite: bool = False,
    ) -> None:
        """Atomically rename a stored key within the same scope.

        Server-side rename on object stores that support it (S3 family,
        Azure); copy + delete fallback on stores that don't.
        ``overwrite=False`` (the default) makes the operation safe-by-
        default: a target that already exists raises rather than
        silently clobbering the file at that key.
        """

        full_src = self._full_key(scope, src_key)
        full_dst = self._full_key(scope, dst_key)
        if full_src == full_dst:
            return
        await obs.rename_async(self._store, full_src, full_dst, overwrite=overwrite)

    async def copy(
        self,
        src_scope: Scope,
        src_key: str,
        dst_scope: Scope,
        dst_key: str,
        *,
        overwrite: bool = False,
    ) -> None:
        """Server-side copy a stored key from one scope to another. Both scopes
        live in the same bucket (distinguished by their key prefix), so this is a
        single object-store CopyObject (Garage / S3 family) — no download/reupload.
        ``overwrite=False`` raises if the destination key already exists."""
        full_src = self._full_key(src_scope, src_key)
        full_dst = self._full_key(dst_scope, dst_key)
        if full_src == full_dst:
            return
        await obs.copy_async(self._store, full_src, full_dst, overwrite=overwrite)

    @property
    def supports_presigned_uploads(self) -> bool:
        """True for object-store backends that can vend a presigned PUT
        URL the browser can hit directly. False for LocalStore — the
        local backend has no HTTP surface, so very large uploads must
        either be rejected or chunked through the API process."""
        # Check the presign store specifically: if the operator set a
        # public endpoint, that's the store actually doing the signing.
        # In the default single-endpoint case _presign_store is _store
        # so this collapses to the original check.
        return isinstance(self._presign_store, S3Store)

    async def presigned_put_url(self, scope: Scope, key: str, expires_in_seconds: int = 3600) -> str:
        """Mint a presigned PUT URL the browser can use to upload bytes
        directly to the object store, bypassing the API's request body
        path. Used for files past the regular-upload size cap so we
        don't ferry hundreds of MB through Python.

        Caller is responsible for the post-upload step that records the
        audit row and triggers conversion — the object store has no
        notion of either.

        Raises ``NotImplementedError`` for backends without HTTP-level
        signing (LocalStore). Callers should gate on
        ``supports_presigned_uploads`` first.
        """
        if not self.supports_presigned_uploads:
            raise NotImplementedError("presigned uploads require an HTTP object store; " "LocalStore is not supported")
        return await obs.sign_async(
            self._presign_store,
            "PUT",
            self._full_key(scope, key),
            timedelta(seconds=expires_in_seconds),
        )

    async def presigned_get_url(
        self, scope: Scope, key: str, expires_in_seconds: int = 900, *, internal: bool = False
    ) -> str:
        """Mint a presigned GET URL for direct download from the object
        store. Mirrors ``presigned_put_url`` — same gating, same signing.

        Streaming through the API works fine for small files but pins a
        worker thread for the entire transfer; for multi-hundred-MB
        artefacts the CLI/clients should hit the object store directly.
        15-minute default TTL is enough for slow upstream links without
        keeping the URL guessable for long.

        ``internal=True`` signs against the in-cluster store endpoint instead
        of the public one — for URLs the worker itself consumes (the SIN
        range-stream read), where the public ingress would be a hairpin.
        """
        if not self.supports_presigned_uploads:
            raise NotImplementedError(
                "presigned downloads require an HTTP object store; " "LocalStore is not supported"
            )
        return await obs.sign_async(
            self._store if internal else self._presign_store,
            "GET",
            self._full_key(scope, key),
            timedelta(seconds=expires_in_seconds),
        )

    async def head(self, scope: Scope, key: str) -> dict | None:
        """Return ``{size, last_modified}`` for a key, or None if missing.
        Used after a direct upload to confirm the object actually
        landed before we audit a "ok" row."""
        try:
            meta = await self._store.head_async(self._full_key(scope, key))
        except FileNotFoundError:
            return None
        size = int(meta["size"]) if isinstance(meta, dict) else int(getattr(meta, "size", 0))
        lm = meta.get("last_modified") if isinstance(meta, dict) else getattr(meta, "last_modified", None)
        lm_iso = lm.isoformat() if hasattr(lm, "isoformat") else (str(lm) if lm else None)
        return {"size": size, "last_modified": lm_iso}

    async def exists(self, scope: Scope, key: str) -> bool:
        try:
            await self._store.head_async(self._full_key(scope, key))
        except FileNotFoundError:
            return False
        return True

    async def stream_to_path(self, scope: Scope, key: str, dest_path: pathlib.Path) -> None:
        """Stream an object to a local file, decompressing gzip on the fly.

        Used by the worker for sources where loading the whole payload
        into RAM is wasteful or impossible (multi-GB SIF result decks,
        for example). The destination is overwritten — caller supplies a
        fresh tempfile path.

        Decompression is wired through ``zlib.decompressobj`` with a
        gzip-aware window so we never hold the full decompressed payload
        in memory; chunks come off the network, get expanded, and go
        straight to disk.
        """
        try:
            result = await self._store.get_async(self._full_key(scope, key))
        except _ObstoreNotFound as exc:
            # Missing key (moved/deleted before the worker fetched it). Surface a
            # plain FileNotFoundError so callers (the conversion worker) report a
            # clean "source not found" instead of the raw obstore retry dump.
            raise FileNotFoundError(f"object not found: {key}") from exc
        chunk_iter = result.stream().__aiter__()

        try:
            first_chunk = bytes(await chunk_iter.__anext__())
        except StopAsyncIteration:
            dest_path.write_bytes(b"")
            return

        is_gzip = first_chunk[:2] == _GZIP_MAGIC
        if is_gzip:
            decomp = zlib.decompressobj(zlib.MAX_WBITS | 16)
            with open(dest_path, "wb") as fh:
                fh.write(decomp.decompress(first_chunk))
                async for chunk in chunk_iter:
                    fh.write(decomp.decompress(bytes(chunk)))
                fh.write(decomp.flush())
        else:
            with open(dest_path, "wb") as fh:
                fh.write(first_chunk)
                async for chunk in chunk_iter:
                    fh.write(bytes(chunk))

    async def open_stream(self, scope: Scope, key: str) -> StreamResult:
        """Open a byte stream eagerly: raises FileNotFoundError immediately
        if the key is missing, then returns an async iterator over chunks
        plus the detected ``Content-Encoding`` (or None).

        The first chunk is peeked to detect the gzip magic; the stream
        is reconstituted so the caller still sees every byte. The
        compressed bytes are *not* expanded — the caller is expected to
        forward ``Content-Encoding: gzip`` so the browser does that.
        """
        try:
            result = await self._store.get_async(self._full_key(scope, key))
        except _ObstoreNotFound as exc:
            raise FileNotFoundError(f"object not found: {key}") from exc

        # Some backends populate this from object metadata; treat it as
        # a hint, but fall back to magic-byte sniffing below either way.
        meta_encoding = None
        attrs = getattr(result, "attributes", None)
        if isinstance(attrs, dict):
            meta_encoding = attrs.get("Content-Encoding") or attrs.get("content-encoding")

        chunk_iter = result.stream().__aiter__()
        try:
            first_chunk = bytes(await chunk_iter.__anext__())
        except StopAsyncIteration:
            first_chunk = b""

        encoding: str | None = None
        if first_chunk[:2] == _GZIP_MAGIC:
            encoding = "gzip"
        elif meta_encoding:
            encoding = meta_encoding

        async def _gen() -> AsyncIterator[bytes]:
            if first_chunk:
                yield first_chunk
            async for chunk in chunk_iter:
                yield bytes(chunk)

        return StreamResult(stream=_gen(), content_encoding=encoding)
