"""Synchronous facades a plugin entrypoint uses from its worker thread: source-node change
recording (DB pool or REST) and a sync view of Storage.
"""

from __future__ import annotations

import asyncio
import datetime
import os
import pathlib
import time

from ada.config import logger

from .. import db as db_module


class _SyncSourceNodesFacade:
    """Synchronous view of the source-node change table, scoped to one job.

    A plugin that drives an external CAD system knows when each node of it last
    changed, and this is how it says so. Bridged onto the worker's event loop the
    same way :class:`_SyncStorageFacade` is, because a plugin entrypoint runs in
    an executor thread.

    Constructed only when the worker HAS a pool. A worker without ``DATABASE_URL``
    is a supported deployment, so the facade is simply not passed and a plugin
    sees the parameter absent -- which it must handle, exactly as it handles a
    storage backend that refuses a write.
    """

    def __init__(self, pool, scope, loop):
        self._pool, self._scope, self._loop = pool, scope, loop

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    @property
    def scope(self) -> str:
        return self._scope_key

    @property
    def _scope_key(self) -> str:
        """The scope's canonical key -- what the reader looks rows up by.

        prefix(), never str(): `Scope` is a dataclass, so str() gives its repr,
        which is not an identifier. Writer and reader would agree on it today and
        both be wrong tomorrow, because adding a field to that dataclass silently
        rewrites the key and orphans every row already stored.
        """
        return self._scope.prefix()

    def record(self, source: str, nodes: list) -> int:
        """Upsert observed nodes for one source. Returns rows written.

        Each node is a dict: ``node_ref`` and ``last_changed_at`` required,
        ``parent_ref`` / ``name`` / ``last_changed_by`` optional. A writer that
        observes a leaf change is expected to stamp every node ABOVE it too --
        the roll-up is the writer's job, so that a consumer asking about a branch
        reads one row rather than walking a hierarchy this table does not model.
        """
        from .. import db as db_module

        return self._run(db_module.record_source_nodes(self._pool, scope=self._scope_key, source=source, nodes=nodes))

    def get(self, source: str, node_refs: list) -> list:
        """What is already recorded, so a writer can avoid restating it."""
        from .. import db as db_module

        return self._run(
            db_module.get_source_nodes(self._pool, scope=self._scope_key, source=source, node_refs=node_refs)
        )

    def sources(self) -> list:
        """What each source has recorded here: ``source``, ``nodes``,
        ``last_changed_at``, ``observed_at``.

        THE CURSOR, for a writer that scans a source for changes. ``get`` answers
        "is this node current", which needs refs the caller already holds;
        this answers "how far have I got", which is what a sweep needs BEFORE it
        knows any refs. Without it a scheduled scan has nowhere to keep a
        watermark and must re-read a fixed window every run -- which is both
        wasteful and unsound, because a window is a guess about how long the gap
        between runs was, and a missed run makes the guess wrong.

        ``last_changed_at`` is the watermark: a MAX over what is recorded, so it
        moves forward only as changes are actually observed.
        """
        from .. import db as db_module

        return self._run(db_module.list_source_node_sources(self._pool, scope=self._scope_key))


class _RestSourceNodesRecorder:
    """The same recording surface as :class:`_SyncSourceNodesFacade`, over HTTP.

    For a worker that has NO database pool. Joining the job queue from outside
    the cluster is a supported deployment, and such a worker is normally run
    without ``DATABASE_URL`` on purpose -- one fewer credential, and no route
    from outside in to the database. But a plugin that drives an external
    source is exactly the kind of work that lands on such a worker, and until
    this existed it could not record anything: the facade IS a pool, so change
    tracking was silently inert precisely where it was most wanted.

    This talks to the API instead, with the same bearer token the worker's
    operator already holds. It is a strictly smaller credential than Postgres,
    and it crosses the same boundary the job queue already crosses.

    Plain ``urllib``, and blocking, deliberately:

    * a plugin entrypoint runs in an executor THREAD, so blocking here blocks
      that thread and nothing else -- unlike the pool facade there is no event
      loop to bridge back onto, which removes the only tricky part;
    * it adds no dependency to a worker that may be installed anywhere.

    Configured by ``ADA_VIEWER_API_URL`` and ``ADA_VIEWER_TOKEN`` -- the names
    an out-of-cluster deployment already uses for API access, so the worker
    reads the credential its host has rather than inventing a second spelling
    of it.
    """

    # Chunked because a roll-up stamps every node ABOVE a changed leaf, so the
    # natural batch is thousands of rows. The write cap matches the route's;
    # the read budget is a URL LENGTH rather than a count, because refs go in a
    # query string and proxies start dropping long ones well before the server
    # would object.
    _WRITE_CHUNK = 2000
    _READ_REF_CHARS = 3000
    _TIMEOUT_S = 60.0
    _ATTEMPTS = 3

    def __init__(self, base_url: str, token: str, scope):
        self._base = base_url.rstrip("/")
        self._token = token
        self._scope = scope

    @property
    def scope(self) -> str:
        return self._scope_key

    @property
    def _scope_key(self) -> str:
        """The same key the pool facade uses -- prefix(), never str(). A
        recorder that keyed rows differently from the other recorder would
        write a second, invisible half of the table."""
        return self._scope.prefix()

    def _url(self, query: str = "") -> str:
        """The route's URL for this scope.

        ``wire()``, NOT ``prefix()``. The prefix is the storage key and contains a
        ``/`` for every scope except shared, which makes the path one segment too
        long: it matches a different route, or none, and comes back 405 -- a status
        that says nothing about scopes and reads as "the API refused the request".
        That was this client's behaviour for every write it ever made.
        """
        from urllib.parse import quote

        return f"{self._base}/api/scopes/{quote(self._scope.wire(), safe=':')}/source-nodes{query}"

    def _request(self, url: str, payload: "dict | None" = None) -> dict:
        """One call, retried on transient failure.

        Retrying is safe because the write is an idempotent upsert (and the
        read is a read). It is worth doing because the caller is typically a
        scheduled sweep over a link nobody controls, and losing a whole sweep
        to one blip means the next one restarts from a colder cursor.
        """
        import json as _json
        import urllib.error
        import urllib.request

        body = None if payload is None else _json.dumps(payload).encode("utf-8")
        last: Exception | None = None
        for attempt in range(self._ATTEMPTS):
            req = urllib.request.Request(url, data=body, method="POST" if body is not None else "GET")
            req.add_header("Authorization", f"Bearer {self._token}")
            req.add_header("Accept", "application/json")
            if body is not None:
                req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=self._TIMEOUT_S) as resp:
                    return _json.loads(resp.read().decode("utf-8") or "{}")
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", "replace")[:500]
                except Exception:
                    pass
                # A 4xx is the request being wrong, and it will be just as
                # wrong next time. Only a server-side or transport failure is
                # worth a second go.
                if exc.code < 500:
                    raise RuntimeError(f"source-node API refused the request ({exc.code}): {detail}") from exc
                last = RuntimeError(f"source-node API error {exc.code}: {detail}")
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = RuntimeError(f"source-node API unreachable: {type(exc).__name__}: {exc}")
            if attempt + 1 < self._ATTEMPTS:
                time.sleep(2**attempt)
        raise last if last is not None else RuntimeError("source-node API call failed")

    @staticmethod
    def _node_json(node: dict) -> dict:
        changed = node.get("last_changed_at")
        if hasattr(changed, "isoformat"):
            if getattr(changed, "tzinfo", None) is None:
                raise ValueError(
                    f"last_changed_at for node {node.get('node_ref')!r} has no timezone. "
                    "Send an aware datetime: this value only ever moves forward, so a "
                    "local wall-clock time read as UTC is a permanently wrong record."
                )
            changed = changed.isoformat()
        out = {"node_ref": node.get("node_ref"), "last_changed_at": changed}
        for field in ("parent_ref", "name", "last_changed_by"):
            if node.get(field) is not None:
                out[field] = node[field]
        return out

    def record(self, source: str, nodes: list) -> int:
        """Upsert observed nodes for one source. Returns rows written.

        The same contract as the pool facade's ``record``, including that the
        writer is expected to stamp every node above a changed leaf.
        ``last_changed_at`` must be timezone-aware: it is serialised to
        ISO-8601 here and the route rejects an offset-less timestamp, because
        that column only ever moves forward and a mis-read value can never be
        corrected downward.
        """
        if not nodes:
            return 0
        written = 0
        for start in range(0, len(nodes), self._WRITE_CHUNK):
            chunk = nodes[start : start + self._WRITE_CHUNK]
            payload = {"source": source, "nodes": [self._node_json(n) for n in chunk]}
            written += int(self._request(self._url(), payload).get("recorded") or 0)
        return written

    def get(self, source: str, node_refs: list) -> list:
        """What is already recorded, so a writer can avoid restating it."""
        from urllib.parse import quote, urlencode

        wanted = [str(r) for r in node_refs if r]
        if not wanted:
            return []
        out: list = []
        batch: list[str] = []
        size = 0
        for ref in wanted + [None]:  # the trailing None flushes the last batch
            if (ref is None or size + len(quote(ref)) > self._READ_REF_CHARS) and batch:
                query = "?" + urlencode({"source": source, "refs": ",".join(batch)})
                out.extend(self._node_from_json(n) for n in self._request(self._url(query)).get("nodes") or [])
                batch, size = [], 0
            if ref is not None:
                batch.append(ref)
                size += len(quote(ref)) + 1
        return out

    def sources(self) -> list:
        """The same cursor read as the pool facade's ``sources``.

        Timestamps are parsed back into aware datetimes so a plugin cannot tell
        the two recorders apart by what they hand back -- the whole point of this
        class being a surface rather than a different API.
        """
        out = []
        for row in self._request(self._url()).get("sources") or []:
            out.append(
                {
                    "source": row.get("source"),
                    "nodes": row.get("nodes"),
                    "last_changed_at": self._parse_dt(row.get("last_changed_at")),
                    "observed_at": self._parse_dt(row.get("observed_at")),
                }
            )
        return out

    @staticmethod
    def _parse_dt(value):
        if not value:
            return None
        try:
            return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            # A timestamp this cannot read is not worth failing a sweep over; the
            # caller treats None as "no cursor" and falls back to a cold start,
            # which is the safe direction (over-read, never skip).
            return None

    @staticmethod
    def _node_from_json(raw: dict):
        """Rebuild the same :class:`~.db.SourceNode` the pool facade returns, so
        a plugin cannot tell the two recorders apart by what they hand back."""

        def _dt(value):
            return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None

        return db_module.SourceNode(
            node_ref=raw.get("node_ref"),
            parent_ref=raw.get("parent_ref"),
            name=raw.get("name"),
            last_changed_at=_dt(raw.get("last_changed_at")),
            last_changed_by=raw.get("last_changed_by"),
            observed_at=_dt(raw.get("observed_at")),
        )


def _rest_source_nodes_config() -> "tuple[str, str] | None":
    """``(base_url, token)`` for API recording, or None if not configured.

    Separate from the recorder so boot can report which way this worker is set
    up without inventing a scope to probe with.
    """
    base = (os.environ.get("ADA_VIEWER_API_URL") or "").strip().rstrip("/")
    token = (os.environ.get("ADA_VIEWER_TOKEN") or "").strip()
    if not base or not token:
        return None
    if not base.startswith(("http://", "https://")):
        # Reported, not ignored: whoever set this meant to enable recording,
        # and silently falling back to "cannot record" is the exact failure
        # this path exists to remove.
        logger.warning(
            "worker: ADA_VIEWER_API_URL=%r is not an http(s) URL; source-node recording stays disabled",
            base,
        )
        return None
    return base, token


def _source_nodes_recorder(db_pool, scope, loop):
    """The recorder this worker can offer a plugin, or None.

    Order matters: a pool is the direct path and wins wherever it exists. The
    REST recorder is the fallback for a worker that has no pool but does have
    API credentials, and ``None`` -- a plugin told plainly that nothing here
    can record -- stays a supported outcome rather than an error, because a
    worker configured for neither is a perfectly ordinary worker.
    """
    if db_pool is not None:
        return _SyncSourceNodesFacade(db_pool, scope, loop)
    cfg = _rest_source_nodes_config()
    return None if cfg is None else _RestSourceNodesRecorder(cfg[0], cfg[1], scope)


class _SyncStorageFacade:
    """Synchronous view of the async :class:`Storage`, scoped to one job.

    A utility handler runs in a worker thread (sync) but needs to read/write
    blobs (fetch a compare-ref build, upload an overlay GLB). This bridges each
    call back onto the worker's event loop via ``run_coroutine_threadsafe`` so
    the handler stays simple, synchronous code.
    """

    def __init__(self, storage, scope, loop):
        self._s, self._scope, self._loop = storage, scope, loop

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def list_keys(self, prefix: str = "") -> list[str]:
        entries = self._run(self._s.list(self._scope))
        return [e.key for e in entries if e.key.startswith(prefix)]

    def fetch_to_path(self, key: str, dest):
        self._run(self._s.stream_to_path(self._scope, key, pathlib.Path(dest)))
        return dest

    def get_bytes(self, key: str) -> bytes:
        return self._run(self._s.get_bytes(self._scope, key))

    def put_bytes(self, key: str, data: bytes, content_encoding: "str | None" = None) -> None:
        self._run(self._s.put_bytes(self._scope, key, data, content_encoding=content_encoding))
