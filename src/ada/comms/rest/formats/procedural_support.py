"""Shared by the procedural handlers: compile-log capture, run-log / run-pointer blobs and
their retention, the catalog fingerprint sidecar, and engine-manifest resolution.
"""

from __future__ import annotations

import contextlib
import datetime
import io
import logging
import threading

import asyncpg

from ada.config import logger

from .. import db as db_module
from ..storage import Storage

# Cap the persisted compile log so a runaway (per-cell) warning storm can't
# balloon the blob; keep the TAIL (the end usually carries the failure).
_COMPILE_LOG_CAP_BYTES = 256 * 1024


class _CompileLogCapture(logging.Handler):
    """In-memory logging handler that buffers records emitted DURING a procedural
    compile so the worker can persist them as an inspectable ``.log`` blob.

    Thread-safe: the compile runs in an executor thread while the event loop keeps
    logging heartbeats on the main thread, so both may ``emit`` concurrently."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        self._lines: list[str] = []
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:
            return
        with self._lock:
            self._lines.append(line)

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self._lines)


@contextlib.contextmanager
def _capture_compile_logs():
    """Attach a :class:`_CompileLogCapture` to the ``ada`` logger (where the
    compile emits — it has ``propagate=False``) and the root logger (where an
    external engine's own logger propagates), forcing INFO level for the duration
    so INFO+ records are captured, then restoring everything on exit."""
    handler = _CompileLogCapture()
    ada_logger = logging.getLogger("ada")
    root_logger = logging.getLogger()
    targets = [ada_logger, root_logger]
    prev_levels = [(lg, lg.level) for lg in targets]
    for lg in targets:
        lg.addHandler(handler)
        # A logger only calls handlers for records at/above its effective level;
        # WARNING-defaulted loggers would drop the INFO messages we want.
        if lg.level == logging.NOTSET or lg.level > logging.INFO:
            lg.setLevel(logging.INFO)
    try:
        yield handler
    finally:
        for lg in targets:
            lg.removeHandler(handler)
        for lg, level in prev_levels:
            lg.setLevel(level)


def _assemble_compile_log(handler: _CompileLogCapture, stdout_buf: io.StringIO, extra: str | None) -> str:
    """Merge captured logging records, any compile stdout, and an optional extra
    section (a traceback on failure) into one bounded text blob (tail-capped)."""
    text = "\n".join(handler.snapshot())
    out = stdout_buf.getvalue().strip()
    if out:
        text = f"{text}\n" if text else ""
        text += f"{'-' * 8} stdout {'-' * 8}\n{out}"
    if extra:
        prefix = f"{text}\n" if text else ""
        text = f"{prefix}{'-' * 8} traceback {'-' * 8}\n{extra.strip()}"
    data = text.encode("utf-8")
    if len(data) > _COMPILE_LOG_CAP_BYTES:
        tail = data[-_COMPILE_LOG_CAP_BYTES:].decode("utf-8", errors="ignore")
        text = f"…[log truncated to last {_COMPILE_LOG_CAP_BYTES // 1024} KB]…\n{tail}"
    return text


def _compile_run_header(
    *,
    run_id: str,
    model_id: str | None,
    revision: object,
    engine: str | None,
    lod: str,
    detailing: str | None,
    is_preview: bool,
    status: str,
) -> str:
    """The banner every compile-run log opens with.

    It is what makes a run log SELF-IDENTIFYING: reading one you can tell which
    run produced it, what it compiled and how it ended — so a log that IS stale
    (an artifact served from cache, whose log belongs to the run that built it)
    announces itself instead of masquerading as the run you just triggered."""
    when = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    what = "preview" if is_preview else f"r{revision}"
    bits = [
        f"run {run_id}",
        f"model {model_id}",
        what,
        f"lod={lod}",
        f"engine={engine or 'adapy-default'}",
    ]
    if detailing and detailing != "none":
        bits.append(f"detailing={detailing}")
    return f"=== compile {status} · {when} · " + " · ".join(bits) + " ==="


async def _put_run_log(storage: "Storage", scope, model_id: str | None, run_id: str, text: str) -> str | None:
    """Persist ONE compile run's log at its run-stamped key, returning that key.

    ALWAYS writes, even when the engine emitted nothing: an empty run still gets a
    blob carrying its banner. The old code skipped the write on empty text, which
    is precisely how a clean recompile left the previous run's failure sitting at
    the shared artifact-derived key for the panel to show. Best-effort — a log
    that fails to upload must not fail an otherwise-good compile."""
    if not model_id:
        return None
    try:
        from ..procedural import procedural_run_log_key

        key = procedural_run_log_key(model_id, run_id)
    except ValueError:
        logger.warning("worker: refusing to write a compile log for unsafe run id %r", run_id)
        return None
    try:
        await storage.put_bytes(scope, key, text.encode("utf-8"), content_encoding="gzip")
        return key
    except Exception:
        logger.exception("worker: procedural compile-run log upload failed for %s", model_id)
        return None


async def _put_run_pointer(storage: "Storage", scope, derived_key: str, run_id: str) -> None:
    """Point an artifact key at the run that most recently targeted it (a ``.run``
    sibling — see procedural.procedural_run_pointer_key).

    Written when the run STARTS, so it is already in place for a run that fails
    before producing bytes; that failure's log is then what the viewer finds for
    the artifact, rather than the last SUCCESS's log. Best-effort: without the
    pointer the log lookup simply falls back to the legacy sibling."""
    try:
        from ..procedural import procedural_run_pointer_key

        await storage.put_bytes(scope, procedural_run_pointer_key(derived_key), run_id.encode("utf-8"))
    except Exception:
        logger.warning("worker: failed to write run-pointer sidecar for %s", derived_key)


async def _prune_run_logs(storage: "Storage", scope, model_id: str | None, keep_key: str = "") -> None:
    """Drop all but the newest ``RUN_LOG_RETENTION`` run logs for one model.

    Run-keyed logs accumulate where the old artifact-keyed log overwrote itself, so
    the prefix needs a bound. Bounded listing (one model's ``runs/`` prefix only)
    and best-effort throughout: a pruning failure is never worth failing a compile
    over, and losing an old run's log only costs its admin audit row the Log tab."""
    if not model_id:
        return
    try:
        from ..procedural import (
            RUN_LOG_RETENTION,
            procedural_run_dir,
            prune_run_log_keys,
        )

        entries = await storage.list_prefix(scope, procedural_run_dir(model_id))
        if len(entries) <= RUN_LOG_RETENTION:
            return
        # Newest first. last_modified is ISO-8601 (lexicographically sortable) when
        # the backend reports one; entries without it sort oldest so they go first.
        ordered = sorted(entries, key=lambda e: (e.last_modified or "", e.key), reverse=True)
        keys = [e.key for e in ordered if e.key != keep_key]
        # The run that just finished heads the list whatever the backend reported
        # for last_modified: the log the caller is about to be handed must survive.
        if keep_key:
            keys.insert(0, keep_key)
        for key in prune_run_log_keys(keys):
            try:
                await storage.delete(scope, key)
            except Exception:
                logger.debug("worker: could not prune stale compile-run log %s", key)
    except Exception:
        logger.warning("worker: compile-run log retention sweep failed for %s", model_id)


async def _write_catalog_fp_sidecar(storage: "Storage", scope, derived_key: str, opts: dict | None) -> None:
    """Record the catalog fingerprint a procedural artifact was built from, as a
    ``.catfp`` sibling of ``derived_key`` (see procedural.procedural_catalog_fp_key).
    The compile/preview/export endpoints read it back to decide whether a cached
    artifact is stale w.r.t. the live equipment/system catalogs. Best-effort and
    only for catalog-dependent models (the endpoint passes ``catalog_fingerprint``
    only when the model places catalog items); a write failure just means the next
    compile treats the cache as stale and rebuilds once."""
    fp = (opts or {}).get("catalog_fingerprint")
    if not fp:
        return
    try:
        from ..procedural import procedural_catalog_fp_key

        await storage.put_bytes(scope, procedural_catalog_fp_key(derived_key), str(fp).encode("utf-8"))
    except Exception:
        logger.warning("worker: failed to write catalog-fp sidecar for %s", derived_key)


def _advertised_engine_doc(engine: str | None) -> dict | None:
    """A manifest-shaped doc for an engine THIS worker advertises itself.

    A self-advertising engine deliberately has no database row -- that is the
    whole point of advertising -- so its manifest has to come from the registry
    the engine populated at import (see ADA_WORKER_PRELOAD and
    ``register_procedural_engine_capabilities``). Returns the same
    ``{kind, entrypoint, worker_capability}`` shape a row's ``doc`` carries, so
    every caller needs nothing beyond the fallback itself.

    Only offerable specs qualify: a spec carrying capability flags alone
    describes an engine the viewer already knows about, not one this worker can
    dispatch to on its own.
    """
    if not engine:
        return None
    from ada.topo_model.engine_catalog import is_offerable, procedural_engine_specs

    for spec in procedural_engine_specs():
        if spec.get("slug") == engine and is_offerable(spec):
            doc: dict = {"kind": "server", "entrypoint": spec["entrypoint"]}
            if spec.get("worker_capability"):
                doc["worker_capability"] = spec["worker_capability"]
            return doc
    return None


async def _resolve_engine_manifest(db_pool: "asyncpg.Pool", row: dict, engine: str | None) -> dict | None:
    """The registry manifest doc for a NON-default, non-builtin engine (its
    ``entrypoint``/``worker_capability``/xlsx-sibling fields), resolved by slug in
    the model's scope. ``None`` for the default/built-in engines."""
    from ada.topo_model.engines import BUILTIN_ENGINES, is_default_engine

    if is_default_engine(engine) or engine in BUILTIN_ENGINES:
        return None
    eng_row = await db_module.get_procedural_engine_by_slug(
        db_pool, scope_kind=row["scope_kind"], scope_id=row["scope_id"], slug=engine
    )
    # A row wins when present -- it is an explicit admin registration and may
    # pin a different entrypoint than whatever this pod happens to run.
    return ((eng_row or {}).get("doc") if eng_row else None) or _advertised_engine_doc(engine)
