"""``export_search_asset`` -- one (provider, collection) batch of attribute-search hits, each written
as an IFC file (plus sidecar, and optionally GLB / STEP / thumbnail) into a target scope + folder.

The API's export run (``routes/asset_exports.py``) fans a search out into one job per batch and
assigns every hit its file key up front, so concurrent jobs never race for a name. This handler does
the work for one batch, hit by hit, and reports per file -- a hit with no geometry, or one whose
write failed, is a row of the summary, not a failed job (``ada.comms.rest.search_export``).

SYNTHETIC: the geometry comes from the geometry provider's concepts reader through the storage
facade of the scope SEARCHED (``job.scope``), and the files go to the TARGET scope, which the
API checked the user may write to before anything was queued.

PHASES ARE TIMED, SUMMED OVER THE BATCH: ``resolve_ms`` (finding the geometry provider's subject
for the hit), ``read_ms`` (the provider reading it into objects), ``attrs_ms`` (the attribute-index
lookups), ``ifc_ms``, ``convert_ms`` (GLB/STEP from the IFC), ``thumb_ms`` and ``upload_ms``.

The summary (``ada.files/export-batch@1``) is stored gzip-at-rest at ``job.derived_key``; the API
reads it back and copies the counts, timings and file errors into the run's item row.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import pathlib
import shutil
import tempfile
import time
import traceback as tb_module
from datetime import datetime, timezone
from typing import Any

from ada.config import logger

from ..queue import JOB_STATUS_DONE, JOB_STATUS_ERROR, Job
from ..search_export import (
    EXPORT_SEARCH_KIND,
    GEOMETRY_DESIGN,
    GEOMETRY_MESH,
    GEOMETRY_PROVIDER,
    GlbProvenance,
    NoGeometry,
    apply_source_attributes,
    element_sources,
    matched_attributes,
    mesh_elements,
    meta_document,
    meta_sidecar_key,
    patch_step_description,
    resolve_geometry,
    stamp_provenance,
    thumb_sidecar_key,
    wrap_in_context,
    write_ifc,
)
from ..worker.audit import _audit_done
from .registry import JobContext, SyntheticFormatHandler

__all__ = ["EXPORT_SEARCH_KIND", "ExportSearchAssetHandler"]

BATCH_SCHEMA = "ada.files/export-batch@1"
#: Pixel size of a thumbnail (square).
THUMB_SIZE = 256


def _ms(t0: float) -> int:
    return round((time.perf_counter() - t0) * 1000)


def _exporter_version() -> str:
    try:
        import ada

        return f"adapy {getattr(ada, '__version__', '?')}"
    except Exception:  # noqa: BLE001
        return "adapy"


def _scope_of(raw: Any):
    from ..scope import Scope

    if not isinstance(raw, dict) or not raw.get("kind"):
        raise ValueError(f"target_scope must be {{kind, id}} (got {raw!r})")
    return Scope.shared() if raw["kind"] == "shared" else Scope(kind=raw["kind"], id=str(raw.get("id") or ""))


def _source_scope_wire(raw: Any, scope) -> str | None:
    """The searched scope in its API path form (``project:<id>`` / ``shared``), as the files carry
    it. The job runs IN that scope, so its own scope is the authority; a user scope has no wire form
    that names it to anyone else (:meth:`Scope.wire` refuses), and then the path segment the
    export was requested under (``user:me``) is the best there is -- right for the user who ran it."""
    try:
        return scope.wire()
    except (ValueError, AssertionError, AttributeError):
        return str(raw) if isinstance(raw, str) and raw.strip() else None


class _PrefixFacade:
    """The job's synchronous storage view, listing by PREFIX: the geometry index lists one
    collection's keys, and the facade the other handlers share lists the whole scope to do that."""

    def __init__(self, storage, scope, loop) -> None:
        from ..worker.source_nodes import _SyncStorageFacade

        self._inner = _SyncStorageFacade(storage, scope, loop)
        self._s, self._scope, self._loop = storage, scope, loop

    def list_keys(self, prefix: str = "") -> list[str]:
        entries = asyncio.run_coroutine_threadsafe(self._s.list_prefix(self._scope, prefix), self._loop).result()
        return [e.key for e in entries if e.key.startswith(prefix)]

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


_THUMB_UNAVAILABLE: list[str | None] = []


def _thumbnail_unavailable() -> str | None:
    """Why this worker cannot render thumbnails, or None when it can. Asked once per process."""
    if not _THUMB_UNAVAILABLE:
        reason = None
        if os.environ.get("ADA_EXPORT_THUMBNAILS", "1").lower() in ("0", "false", "no", "off"):
            reason = "thumbnails disabled (ADA_EXPORT_THUMBNAILS=0)"
        else:
            try:
                import ada.visit.rendering.pygfx_offscreen_utils  # noqa: F401
            except Exception as exc:  # noqa: BLE001 - pygfx/wgpu absent in this worker
                reason = f"no offscreen renderer in this worker, thumbnails skipped ({type(exc).__name__}: {exc})"
        _THUMB_UNAVAILABLE.append(reason)
    return _THUMB_UNAVAILABLE[0]


def _render_thumb(glb_path: pathlib.Path, out_path: pathlib.Path) -> str | None:
    """A ``THUMB_SIZE`` PNG of the GLB, or the reason there is none. The offscreen renderer is an
    optional dependency of the worker image: without it the export goes on without thumbnails."""
    reason = _thumbnail_unavailable()
    if reason is not None:
        return reason
    from ada.visit.rendering.pygfx_offscreen_utils import glb_to_image

    try:
        image = glb_to_image(glb_path, size=(THUMB_SIZE, THUMB_SIZE))
        image.save(out_path, format="PNG")
    except Exception as exc:  # noqa: BLE001 - a thumbnail is a nicety
        return f"thumbnail render failed ({type(exc).__name__}: {exc})"
    return None


def _convert(ifc_path: pathlib.Path, fmt: str, out_dir: pathlib.Path) -> pathlib.Path:
    """GLB/STEP from the written IFC through the converter registry -- the same ``convert()`` an
    uploaded ``.ifc`` goes through -- materialised as a file in ``out_dir``."""
    from ..converter import convert

    result = convert(ifc_path, ifc_path.name, fmt)
    out = out_dir / f"{ifc_path.stem}.{fmt}"
    if isinstance(result, pathlib.Path):
        shutil.move(str(result), out)
    else:
        out.write_bytes(bytes(result))
    return out


class ExportSearchAssetHandler(SyntheticFormatHandler):
    kind = EXPORT_SEARCH_KIND
    # The cached-blob short-circuit can never fire: the summary's key is per (run, batch).

    async def run(self, job: Job, ctx: JobContext) -> None:
        opts = job.conversion_options or {}
        try:
            target_scope = _scope_of(opts.get("target_scope"))
        except ValueError as exc:
            await self._fail(job, ctx, str(exc))
            return
        hits = [h for h in (opts.get("hits") or []) if isinstance(h, dict) and h.get("file")]
        provider = str(opts.get("provider") or "")
        collection = str(opts.get("collection") or "")
        if not hits or not collection:
            await self._fail(job, ctx, f"{EXPORT_SEARCH_KIND} needs 'collection' and 'hits' with file keys")
            return
        export = self._export_mesh if opts.get("geometry") == GEOMETRY_MESH else self._export
        try:
            summary = await export(job, ctx, opts, hits, provider, collection, target_scope)
        except Exception as exc:  # noqa: BLE001 - the batch's failure, reported as the job's
            logger.exception("worker: %s failed for job %s", EXPORT_SEARCH_KIND, job.job_id)
            await self._fail(job, ctx, str(exc), tb_module.format_exc())
            return
        if summary is None:  # cancelled
            return
        await ctx.storage.put_bytes(
            ctx.scope, job.derived_key, json.dumps(summary).encode("utf-8"), content_encoding="gzip"
        )
        await ctx.queue.update(job.job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
        await _audit_done(ctx.db_pool, job.job_id, "done", None, ctx.started_at)

    async def _fail(self, job: Job, ctx: JobContext, msg: str, trace: str | None = None) -> None:
        await ctx.queue.update(job.job_id, status=JOB_STATUS_ERROR, stage="export", error=msg)
        await _audit_done(ctx.db_pool, job.job_id, "error", msg, ctx.started_at, traceback=trace)

    async def _cancelled(self, job: Job, ctx: JobContext) -> bool:
        try:
            current = await ctx.queue.get(job.job_id)
        except Exception:  # noqa: BLE001
            return False
        return current is not None and str(current.status).lower() == "cancelled"

    async def _export(self, job, ctx, opts, hits, provider, collection, target_scope) -> dict | None:
        from ada.clash.geometry_source import GeometrySourceIndex

        from .. import db as db_module

        loop = asyncio.get_running_loop()
        source = _PrefixFacade(ctx.storage, ctx.scope, loop)
        index = GeometrySourceIndex(source)
        geometry_provider = str(opts.get("geometry_provider") or GEOMETRY_PROVIDER)
        include_attrs = bool(opts.get("include_source_attributes", True))
        also = [f for f in (opts.get("also") or []) if f in ("glb", "step")]
        query = opts.get("query") or {}
        run_id = opts.get("run_id")
        scope_key = ctx.scope.prefix()
        source_scope = _source_scope_wire(opts.get("source_scope"), ctx.scope)
        created_by = opts.get("requested_by")
        timings = {k: 0 for k in ("resolve_ms", "read_ms", "attrs_ms", "ifc_ms", "convert_ms", "thumb_ms", "upload_ms")}
        warnings: list[str] = []
        files: list[dict] = []

        revisions: dict[tuple[str, str], str | None] = {}
        if ctx.db_pool is not None:
            with contextlib.suppress(Exception):
                for c in await db_module.list_attr_collections(ctx.db_pool, scope=scope_key):
                    revisions[(c["provider"], c["collection"])] = c.get("revision")
        else:
            warnings.append("this worker has no database: source attributes and matched values were not read")

        async def lookup(el_rows_provider: str, elements) -> list[dict]:
            if ctx.db_pool is None:
                return []
            return await db_module.lookup_attr_rows(
                ctx.db_pool,
                scope=scope_key,
                provider=el_rows_provider,
                collection=collection,
                node_ids=[e.node_id for e in elements if e.node_id],
                full_names=[n for e in elements for n in e.lookup_names()],
                source_refs=[e.source_ref for e in elements if e.source_ref],
            )

        thumb_note_logged = False
        for n, hit in enumerate(hits):
            if await self._cancelled(job, ctx):
                logger.info("worker: %s job %s cancelled after %d file(s)", EXPORT_SEARCH_KIND, job.job_id, n)
                return None
            await ctx.queue.update(job.job_id, stage=f"file {n + 1}/{len(hits)}", progress=n / max(1, len(hits)))
            key = str(hit["file"])
            row = {"file": key, "node_id": hit.get("node_id"), "full_name": hit.get("full_name"), "status": "written"}
            work = pathlib.Path(tempfile.mkdtemp(prefix="ada-search-export-"))
            try:
                t = time.perf_counter()
                try:
                    manifest, node = await loop.run_in_executor(None, resolve_geometry, index, collection, hit)
                except NoGeometry as exc:
                    timings["resolve_ms"] += _ms(t)
                    row.update(status="no_geometry", error=str(exc))
                    files.append(row)
                    continue
                timings["resolve_ms"] += _ms(t)

                t = time.perf_counter()
                from ada.assets.concepts import part_for_manifest

                part = await loop.run_in_executor(
                    None, lambda m=manifest, nd=node: part_for_manifest(m, storage=source, node=nd)
                )
                timings["read_ms"] += _ms(t)
                model = wrap_in_context(part, hit)

                t = time.perf_counter()
                rows: list[dict] = []
                for prov in dict.fromkeys([provider, geometry_provider]):
                    rows.extend(await lookup(prov, model.elements))
                timings["attrs_ms"] += _ms(t)
                hit_row = next((r for r in rows if r.get("node_id") == hit.get("node_id")), None) or next(
                    (r for r in rows if r.get("full_name") == hit.get("full_name")), None
                )
                matched = matched_attributes(query, (hit_row or {}).get("attrs") or hit.get("attrs"))
                attributes = apply_source_attributes(
                    model,
                    rows,
                    provider=provider,
                    collection=collection,
                    include_attributes=include_attrs,
                    scope=source_scope,
                )
                revision = revisions.get((provider, collection))
                provenance = {
                    "provider": provider,
                    "collection": collection,
                    "source_scope": source_scope,
                    "revision": revision,
                    "geometry_provider": manifest.provider,
                    "geometry_subject": manifest.subject,
                    "geometry_revision": manifest.revision,
                    "query": json.dumps(query, ensure_ascii=False, sort_keys=True),
                    "exported_at": datetime.now(timezone.utc).isoformat(),
                    "exporter": _exporter_version(),
                    "run_id": run_id,
                }
                stamp_provenance(
                    model,
                    provider=provider,
                    collection=collection,
                    provenance=provenance,
                    matched=matched,
                    scope=source_scope,
                )

                t = time.perf_counter()
                ifc_path = work / pathlib.PurePosixPath(key).name
                await loop.run_in_executor(None, write_ifc, model, ifc_path)
                timings["ifc_ms"] += _ms(t)

                # ``mesh``: the quick-look GLB this design file was requested from, when it was (it shares
                # this file's stem, so its sidecar is this one from now on).
                derived: dict[str, str | None] = {"glb": None, "step": None, "thumb": None, "mesh": hit.get("mesh_file")}
                local: dict[str, pathlib.Path] = {}
                t = time.perf_counter()
                glb_path = None
                # The thumbnail is rendered from the GLB, so one is made whenever a thumbnail can be.
                if "glb" in also or _thumbnail_unavailable() is None:
                    try:
                        glb_path = await loop.run_in_executor(None, _convert, ifc_path, "glb", work)
                        glb_bytes = glb_path.read_bytes()
                        from ada.assets.build import patch_glb_provenance

                        glb_path.write_bytes(
                            patch_glb_provenance(
                                glb_bytes,
                                GlbProvenance(
                                    {**provenance, "node_id": hit.get("node_id"), "full_name": hit.get("full_name")}
                                ),
                            )
                        )
                        if "glb" in also:
                            local["glb"] = glb_path
                    except Exception as exc:  # noqa: BLE001 - a derived format failing is the file's note
                        glb_path = None
                        if "glb" in also:
                            row["derived_error"] = f"glb: {exc}"
                if "step" in also:
                    try:
                        step_path = await loop.run_in_executor(None, _convert, ifc_path, "step", work)
                        patch_step_description(
                            step_path,
                            [
                                f"ADA export of {hit.get('full_name') or hit.get('node_id')}",
                                f"provider={provider}; collection={collection}; node_id={hit.get('node_id')}",
                                f"revision={revision}; run_id={run_id}",
                            ],
                        )
                        local["step"] = step_path
                    except Exception as exc:  # noqa: BLE001
                        row["derived_error"] = "; ".join(filter(None, [row.get("derived_error"), f"step: {exc}"]))
                timings["convert_ms"] += _ms(t)

                t = time.perf_counter()
                if glb_path is not None:
                    thumb = work / "thumb.png"
                    note = await loop.run_in_executor(None, _render_thumb, glb_path, thumb)
                    if note is None:
                        local["thumb"] = thumb
                    elif not thumb_note_logged:
                        warnings.append(note)
                        thumb_note_logged = True
                elif (reason := _thumbnail_unavailable()) is not None and not thumb_note_logged:
                    warnings.append(reason)
                    thumb_note_logged = True
                timings["thumb_ms"] += _ms(t)

                t = time.perf_counter()
                await ctx.storage.put_path(target_scope, key, ifc_path, content_encoding="gzip")
                for fmt, path in local.items():
                    dest = thumb_sidecar_key(key) if fmt == "thumb" else key.rsplit(".", 1)[0] + f".{fmt}"
                    await ctx.storage.put_path(
                        target_scope, dest, path, content_encoding="gzip" if fmt == "step" else None
                    )
                    derived[fmt] = dest
                if "glb" in local:
                    # Also where the viewer looks for the IFC's own conversion, so opening the IFC
                    # needs no convert job. Stored gzip-at-rest like every converted GLB.
                    from ..converters.keys import derived_key_for

                    await ctx.storage.put_path(
                        target_scope, derived_key_for(key, "glb"), local["glb"], content_encoding="gzip"
                    )
                meta = meta_document(
                    file=key,
                    fmt="ifc",
                    created_by=created_by,
                    source={
                        "provider": provider,
                        "scope": source_scope,
                        "collection": collection,
                        "node_id": hit.get("node_id"),
                        "full_name": hit.get("full_name"),
                        "source_ref": hit.get("source_ref") or (hit_row or {}).get("source_ref"),
                        "revision": revision,
                        "path": hit.get("path") or [],
                    },
                    geometry={
                        "kind": GEOMETRY_DESIGN,
                        "provider": manifest.provider,
                        "subject": manifest.subject,
                        "revision": manifest.revision,
                        "node": node,
                    },
                    query=query,
                    matched=matched,
                    run_id=run_id,
                    attributes=attributes,
                    derived=derived,
                    elements=element_sources(model),
                )
                await ctx.storage.put_bytes(
                    target_scope,
                    meta_sidecar_key(key),
                    json.dumps(meta, indent=1).encode("utf-8"),
                    content_encoding=None,
                )
                timings["upload_ms"] += _ms(t)
                row.update(
                    meta=meta_sidecar_key(key),
                    thumb=derived["thumb"],
                    glb=derived["glb"],
                    step=derived["step"],
                    attributes=attributes,
                )
                files.append(row)
            except Exception as exc:  # noqa: BLE001 - one file's failure is its row
                logger.warning("worker: %s %s failed: %s", EXPORT_SEARCH_KIND, key, exc, exc_info=True)
                row.update(status="error", error=f"{type(exc).__name__}: {exc}")
                files.append(row)
            finally:
                shutil.rmtree(work, ignore_errors=True)

        counts = {
            "written": sum(1 for f in files if f["status"] == "written"),
            "no_geometry": sum(1 for f in files if f["status"] == "no_geometry"),
            "failed": sum(1 for f in files if f["status"] == "error"),
        }
        return {
            "schema": BATCH_SCHEMA,
            "provider": provider,
            "collection": collection,
            "files": files,
            "counts": counts,
            "timings": timings,
            "warnings": warnings,
        }

    async def _export_mesh(self, job, ctx, opts, hits, provider, collection, target_scope) -> dict | None:
        """``geometry: "mesh"``: each hit's GLB as the asset build cut it from what the hit's own
        provider published.

        The API built every hit before submitting this (``routes/asset_exports.py``), so a hit
        arrives with ``mesh`` -- the build's ``glb_key`` and what it was built from -- or with
        ``mesh_error``, the build's reason there is nothing to draw (a ``no_geometry`` row). The
        GLB is copied with the export's provenance in ``asset.extras``, beside its sidecar and
        thumbnail; nothing is converted.
        """
        from ada.assets.build import patch_glb_provenance

        loop = asyncio.get_running_loop()
        query = opts.get("query") or {}
        run_id = opts.get("run_id")
        source_scope = _source_scope_wire(opts.get("source_scope"), ctx.scope)
        created_by = opts.get("requested_by")
        timings = {k: 0 for k in ("read_ms", "thumb_ms", "upload_ms")}
        warnings: list[str] = []
        files: list[dict] = []

        revision = None
        if ctx.db_pool is not None:
            from .. import db as db_module

            with contextlib.suppress(Exception):
                for c in await db_module.list_attr_collections(ctx.db_pool, scope=ctx.scope.prefix()):
                    if (c["provider"], c["collection"]) == (provider, collection):
                        revision = c.get("revision")

        thumb_note_logged = False
        for n, hit in enumerate(hits):
            if await self._cancelled(job, ctx):
                logger.info("worker: %s job %s cancelled after %d file(s)", EXPORT_SEARCH_KIND, job.job_id, n)
                return None
            await ctx.queue.update(job.job_id, stage=f"file {n + 1}/{len(hits)}", progress=n / max(1, len(hits)))
            key = str(hit["file"])
            row = {"file": key, "node_id": hit.get("node_id"), "full_name": hit.get("full_name"), "status": "written"}
            mesh = hit.get("mesh") if isinstance(hit.get("mesh"), dict) else None
            if mesh is None or not mesh.get("glb_key"):
                row.update(status="no_geometry", error=str(hit.get("mesh_error") or f"{provider} published no mesh for it"))
                files.append(row)
                continue
            work = pathlib.Path(tempfile.mkdtemp(prefix="ada-search-export-"))
            try:
                t = time.perf_counter()
                data = await ctx.storage.get_bytes(ctx.scope, str(mesh["glb_key"]))
                if data[:2] == b"\x1f\x8b":
                    import gzip

                    data = gzip.decompress(data)
                timings["read_ms"] += _ms(t)
                provenance = {
                    "provider": provider,
                    "collection": collection,
                    "source_scope": source_scope,
                    "revision": revision,
                    "geometry_kind": GEOMETRY_MESH,
                    "geometry_provider": mesh.get("provider") or provider,
                    "geometry_subject": mesh.get("subject"),
                    "geometry_revision": mesh.get("revision"),
                    "query": json.dumps(query, ensure_ascii=False, sort_keys=True),
                    "exported_at": datetime.now(timezone.utc).isoformat(),
                    "exporter": _exporter_version(),
                    "run_id": run_id,
                    "node_id": hit.get("node_id"),
                    "full_name": hit.get("full_name"),
                }
                glb_path = work / pathlib.PurePosixPath(key).name
                glb_path.write_bytes(patch_glb_provenance(data, GlbProvenance(provenance)))

                t = time.perf_counter()
                thumb = work / "thumb.png"
                note = await loop.run_in_executor(None, _render_thumb, glb_path, thumb)
                if note is not None and not thumb_note_logged:
                    warnings.append(note)
                    thumb_note_logged = True
                timings["thumb_ms"] += _ms(t)

                t = time.perf_counter()
                await ctx.storage.put_path(target_scope, key, glb_path, content_encoding=None)
                derived: dict[str, str | None] = {"glb": key, "thumb": None}
                if note is None:
                    derived["thumb"] = thumb_sidecar_key(key)
                    await ctx.storage.put_path(target_scope, derived["thumb"], thumb, content_encoding=None)
                meta = meta_document(
                    file=key,
                    fmt="glb",
                    created_by=created_by,
                    source={
                        "provider": provider,
                        "scope": source_scope,
                        "collection": collection,
                        "node_id": hit.get("node_id"),
                        "full_name": hit.get("full_name"),
                        "source_ref": hit.get("source_ref"),
                        "revision": revision,
                        "path": hit.get("path") or [],
                    },
                    geometry={
                        "kind": GEOMETRY_MESH,
                        "provider": mesh.get("provider") or provider,
                        "subject": mesh.get("subject"),
                        "revision": mesh.get("revision"),
                        "node": mesh.get("node"),
                    },
                    query=query,
                    matched=matched_attributes(query, hit.get("attrs")),
                    run_id=run_id,
                    attributes={},
                    derived=derived,
                    elements=mesh_elements(hit, provider=provider, collection=collection, scope=source_scope),
                )
                await ctx.storage.put_bytes(
                    target_scope,
                    meta_sidecar_key(key),
                    json.dumps(meta, indent=1).encode("utf-8"),
                    content_encoding=None,
                )
                timings["upload_ms"] += _ms(t)
                row.update(meta=meta_sidecar_key(key), thumb=derived["thumb"], glb=key)
                files.append(row)
            except Exception as exc:  # noqa: BLE001 - one file's failure is its row
                logger.warning("worker: %s %s failed: %s", EXPORT_SEARCH_KIND, key, exc, exc_info=True)
                row.update(status="error", error=f"{type(exc).__name__}: {exc}")
                files.append(row)
            finally:
                shutil.rmtree(work, ignore_errors=True)

        counts = {
            "written": sum(1 for f in files if f["status"] == "written"),
            "no_geometry": sum(1 for f in files if f["status"] == "no_geometry"),
            "failed": sum(1 for f in files if f["status"] == "error"),
        }
        return {
            "schema": BATCH_SCHEMA,
            "provider": provider,
            "collection": collection,
            "geometry": GEOMETRY_MESH,
            "files": files,
            "counts": counts,
            "timings": timings,
            "warnings": warnings,
        }
