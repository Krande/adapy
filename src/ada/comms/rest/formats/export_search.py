"""``export_search_asset`` -- one (provider, collection) batch of attribute-search hits, each written
as an IFC file (plus sidecar, and optionally GLB / STEP / thumbnail) into a target scope + folder --
or, with ``merge``, the whole batch as ONE file (``_export_merged`` / ``_export_mesh_merged``).

The API's export run (``routes/asset_exports.py``) fans a search out into one job per batch and
assigns every hit its file key up front, so concurrent jobs never race for a name. This handler does
the work for one batch, hit by hit, and reports per file -- a hit with no geometry, or one whose
write failed, is a row of the summary, not a failed job (``ada.comms.rest.search_export``).

SYNTHETIC: the geometry comes from the geometry provider's concepts reader through the storage
facade of the scope the hits LIE in -- the searched scope (``job.scope``), or the ``origin_scope`` the
API names for a batch read from another scope's partition (:func:`_origin_of`), whose attribute rows
and revision are read there too -- and the files go to the TARGET scope, which the API checked the
user may write to before anything was queued.

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
from ada.visit.gltf.materials import default_black_materials_glb

from ..queue import JOB_STATUS_DONE, JOB_STATUS_ERROR, Job
from ..search_export import (
    EXPORT_SEARCH_KIND,
    GEOMETRY_DESIGN,
    GEOMETRY_MESH,
    GEOMETRY_PROVIDER,
    GlbProvenance,
    NoGeometry,
)
from ..search_export import _name as _hit_name
from ..search_export import (
    apply_source_attributes,
    element_sources,
    joint_thumb_key,
    matched_attributes,
    merged_mesh_elements,
    mesh_elements,
    meta_document,
    meta_sidecar_key,
    patch_step_description,
    resolve_geometry,
    stamp_matched,
    stamp_provenance,
    thumb_sidecar_key,
    wrap_all_in_context,
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


def _origin_of(opts: dict, scope):
    """The scope the batch's hits are READ from: ``origin_scope`` (a storage prefix) when the API
    named one -- the hits' partition lies in another scope than the export's (shared into it, or an
    admin's search of every scope) -- else the job's own. Read only; the files go to the target."""
    from ..scope import Scope

    raw = opts.get("origin_scope")
    if isinstance(raw, str) and raw and raw != scope.prefix():
        return Scope.from_prefix(raw)
    return scope


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
        # The renderer reads plain glTF only; a built (meshopt-packed) GLB is unpacked first.
        image = glb_to_image(_plain_glb(glb_path), size=(THUMB_SIZE, THUMB_SIZE))
        image.save(out_path, format="PNG")
    except Exception as exc:  # noqa: BLE001 - a thumbnail is a nicety
        return f"thumbnail render failed ({type(exc).__name__}: {exc})"
    return None


def _batch_summary(
    provider: str, collection: str, files: list[dict], timings: dict, warnings: list[str], *, merged: dict | None
) -> dict:
    """``ada.files/export-batch@1`` for a merged batch: the per-hit rows (each written one naming
    the merged file) and ``merged`` -- the one file, or None when no hit made it into one."""
    return {
        "schema": BATCH_SCHEMA,
        "provider": provider,
        "collection": collection,
        "files": files,
        "counts": {
            "written": sum(1 for f in files if f["status"] == "written"),
            "no_geometry": sum(1 for f in files if f["status"] == "no_geometry"),
            "failed": sum(1 for f in files if f["status"] == "error"),
        },
        "merged": merged,
        "timings": timings,
        "warnings": warnings,
    }


def _glb_is_meshopt_packed(path: pathlib.Path) -> bool:
    import struct

    with path.open("rb") as fh:
        head = fh.read(20)
        if len(head) < 20 or head[:4] != b"glTF":
            return False
        (json_len,) = struct.unpack_from("<I", head, 12)
        doc = json.loads(fh.read(json_len).decode("utf-8"))
    return "EXT_meshopt_compression" in (doc.get("extensionsUsed") or [])


def _plain_glb(path: pathlib.Path) -> pathlib.Path:
    """``path`` as plain glTF: itself, or -- meshopt-packed -- an unpacked copy beside it."""
    path = pathlib.Path(path)
    if not _glb_is_meshopt_packed(path):
        return path
    from ada.visit.gltf.meshopt import meshopt_decompress_glb

    plain = path.with_name(f"{path.stem}.plain.glb")
    if not plain.exists():
        meshopt_decompress_glb(path, plain)
    return plain


def _glb_json(path: pathlib.Path) -> dict:
    import struct

    data = path.read_bytes()
    (json_len,) = struct.unpack_from("<I", data, 12)
    return json.loads(data[20 : 20 + json_len].decode("utf-8"))


def _with_scene_extras(data: bytes, extras: dict) -> bytes:
    """``data`` (a GLB) with its scene's ``extras`` replaced -- only the JSON chunk is rewritten."""
    import struct

    (json_len,) = struct.unpack_from("<I", data, 12)
    doc = json.loads(data[20 : 20 + json_len].decode("utf-8"))
    doc["scenes"][doc.get("scene", 0)]["extras"] = extras
    body = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    body += b" " * (-len(body) % 4)
    rest = data[20 + json_len :]
    return (
        b"glTF"
        + struct.pack("<II", 2, 20 + len(body) + len(rest))
        + struct.pack("<I4s", len(body), b"JSON")
        + body
        + rest
    )


def _merge_glbs(parts: list[tuple[dict, pathlib.Path]], out: pathlib.Path, root_name: str) -> list[list[int]]:
    """Every ``(hit, glb)`` of ``parts`` as ONE GLB at ``out`` that picks as each of them did: every
    object (plate, member, ...) of every hit still its own clickable element.

    What makes a built GLB pick per object is in its SCENE EXTRAS, not its geometry: ``id_hierarchy``
    (``id -> [name, parent id]``, the tree the viewer shows) and ``draw_ranges_node<k>`` (``id ->
    [first index, index count]`` within the mesh of glTF node ``node<k>``). Copying only the
    triangles -- as this did at first -- left each hit one unpickable lump. So per source mesh:

    * its triangles are copied UNPROCESSED (``process=False``: no vertex merge, no face reorder), so
      a range's index span still covers the same triangles, under a new node ``node<k>`` (the name
      the viewer pairs with ``draw_ranges_node<k>``);
    * its ids are mapped into ONE id space: an entry is ``(name, mapped parent)``, so the ancestors
      several hits share (their site, frameworks, ...) collapse into one, as the IFC merge shares its
      levels, while each hit's own objects stay apart;
    * its draw ranges are carried over under the mapped ids. A source mesh with none is one element,
      named after its hit.

    ``up_axis`` is carried too (the built GLBs are cut Z-up). Unpacked first -- the built GLBs are
    meshopt-packed and the scene loader reads only plain glTF -- and packed again on the way out by
    the structure-preserving packer, which leaves the JSON (and so these extras) as it is.

    Returns each part's ``[first, last]`` draw-range id (inclusive; ``[]`` for a part with none): a
    part's ids are handed out in one run while it is read, after those of the ancestors it shares
    with earlier parts, so no other part's object falls in it -- what isolates that hit in the
    merged file (the gallery steps through a merged file hit by hit with it).
    """
    import trimesh

    from ada.visit.gltf.meshopt import meshopt_compress_glb

    from ..search_export import _name

    scene = trimesh.Scene()
    hierarchy: dict[str, list] = {}
    ids: dict[tuple[str, str], str] = {}
    draw_ranges: dict[str, dict] = {}
    up_axis = None

    def global_id(name: str, parent: str) -> str:
        key = (name, parent)
        if key not in ids:
            ids[key] = str(len(ids) + 1)
            hierarchy[ids[key]] = [name, parent]
        return ids[key]

    node_n = 0
    spans: list[list[int]] = []
    for hit, path in parts:
        own: list[int] = []  # the ids of this part's draw ranges
        plain = _plain_glb(path)
        doc = _glb_json(plain)
        extras = doc.get("scenes", [{}])[doc.get("scene", 0)].get("extras") or {}
        up_axis = up_axis or extras.get("up_axis")
        source_ids = {str(k): v for k, v in (extras.get("id_hierarchy") or {}).items()}
        local: dict[str, str] = {}

        def mapped(sid: str, seen: frozenset = frozenset()) -> str:
            if sid in local:
                return local[sid]
            name, parent = (source_ids.get(sid) or [sid, "*"])[:2]
            parent = str(parent)
            if parent in ("*", "", "None") or parent not in source_ids or parent in seen or parent == sid:
                gparent = "*"
            else:
                gparent = mapped(parent, seen | {sid})
            local[sid] = global_id(str(name), gparent)
            return local[sid]

        for sid in source_ids:
            mapped(sid)

        source = trimesh.load(str(plain), file_type="glb", force="scene", process=False)
        for node_name in source.graph.nodes_geometry:
            transform, geom_name = source.graph[node_name]
            geometry = source.geometry[geom_name]
            node_n += 1
            new_name = f"node{node_n}"
            scene.add_geometry(geometry, node_name=new_name, geom_name=new_name, transform=transform)
            ranges = extras.get(f"draw_ranges_{node_name}") or {}
            if ranges:
                draw_ranges[f"draw_ranges_{new_name}"] = {mapped(str(rid)): list(span) for rid, span in ranges.items()}
            else:
                faces = getattr(geometry, "faces", None)
                count = 3 * len(faces) if faces is not None else 0
                draw_ranges[f"draw_ranges_{new_name}"] = {global_id(_name(hit), "*"): [0, count]}
            own.extend(int(gid) for gid in draw_ranges[f"draw_ranges_{new_name}"])
        spans.append([min(own), max(own)] if own else [])

    plain_out = out.with_name(f"{out.stem}.plain.glb")
    scene_extras: dict = {"id_hierarchy": hierarchy, **draw_ranges}
    if up_axis:
        scene_extras["up_axis"] = up_axis
    plain_out.write_bytes(_with_scene_extras(scene.export(file_type="glb"), scene_extras))
    packed = meshopt_compress_glb(plain_out, out)
    # The packer hands its input back when it cannot or need not pack (a tiny file, say).
    if pathlib.Path(packed) != out:
        shutil.copyfile(plain_out, out)
    return spans


def _cut_subtrees(glb_path: pathlib.Path, names: list[str], out_dir: pathlib.Path) -> list[pathlib.Path | None]:
    """Per name of ``names``: a GLB of just that node's subtree in ``glb_path`` -- the objects whose
    draw ranges sit under the first ``id_hierarchy`` node so named -- or None when no node is.

    What renders one hit's thumbnail out of a merged file built from an IFC, where no per-hit GLB
    ever existed. The source is read once, whatever the number of names."""
    import numpy as np
    import trimesh

    plain = _plain_glb(glb_path)
    doc = _glb_json(plain)
    extras = doc.get("scenes", [{}])[doc.get("scene", 0)].get("extras") or {}
    hierarchy = {str(k): v for k, v in (extras.get("id_hierarchy") or {}).items()}
    children: dict[str, list[str]] = {}
    first_named: dict[str, str] = {}
    for nid, entry in hierarchy.items():
        name, parent = (list(entry) + [None, None])[:2]
        children.setdefault(str(parent), []).append(nid)
        first_named.setdefault(str(name), nid)
    source = trimesh.load(str(plain), file_type="glb", force="scene", process=False)
    meshes = []
    for node_name in source.graph.nodes_geometry:
        transform, geom_name = source.graph[node_name]
        ranges = {str(k): v for k, v in (extras.get(f"draw_ranges_{node_name}") or {}).items()}
        meshes.append((transform, source.geometry[geom_name], ranges))

    out: list[pathlib.Path | None] = []
    for n, name in enumerate(names):
        root = first_named.get(str(name))
        if root is None:
            out.append(None)
            continue
        want: set[str] = set()
        stack = [root]
        while stack:
            nid = stack.pop()
            if nid not in want:
                want.add(nid)
                stack.extend(children.get(nid, ()))
        scene = trimesh.Scene()
        for transform, geometry, ranges in meshes:
            faces = [
                np.arange(start // 3, (start + count) // 3) for rid, (start, count) in ranges.items() if rid in want
            ]
            if not faces or getattr(geometry, "faces", None) is None:
                continue
            scene.add_geometry(geometry.submesh([np.concatenate(faces)], append=True), transform=transform)
        if not scene.geometry:
            out.append(None)
            continue
        path = out_dir / f"joint-{n}.glb"
        path.write_bytes(scene.export(file_type="glb"))
        out.append(path)
    return out


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
        mesh, merge = opts.get("geometry") == GEOMETRY_MESH, bool(opts.get("merge"))
        export = {
            (False, False): self._export,
            (True, False): self._export_mesh,
            (False, True): self._export_merged,
            (True, True): self._export_mesh_merged,
        }[(mesh, merge)]
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
        origin = _origin_of(opts, ctx.scope)
        source = _PrefixFacade(ctx.storage, origin, loop)
        index = GeometrySourceIndex(source)
        geometry_provider = str(opts.get("geometry_provider") or GEOMETRY_PROVIDER)
        include_attrs = bool(opts.get("include_source_attributes", True))
        also = [f for f in (opts.get("also") or []) if f in ("glb", "step")]
        query = opts.get("query") or {}
        run_id = opts.get("run_id")
        scope_key = origin.prefix()
        source_scope = _source_scope_wire(opts.get("source_scope"), origin)
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

        thumb_logged: list[bool] = [False]
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

                derived = await self._derive_and_upload(
                    ctx,
                    work=work,
                    key=key,
                    ifc_path=ifc_path,
                    also=also,
                    glb_provenance={**provenance, "node_id": hit.get("node_id"), "full_name": hit.get("full_name")},
                    step_lines=[
                        f"ADA export of {hit.get('full_name') or hit.get('node_id')}",
                        f"provider={provider}; collection={collection}; node_id={hit.get('node_id')}",
                        f"revision={revision}; run_id={run_id}",
                    ],
                    target_scope=target_scope,
                    row=row,
                    warnings=warnings,
                    thumb_logged=thumb_logged,
                    timings=timings,
                    mesh_file=hit.get("mesh_file"),
                )
                t = time.perf_counter()
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

    async def _derive_and_upload(
        self,
        ctx,
        *,
        work: pathlib.Path,
        key: str,
        ifc_path: pathlib.Path,
        also: list[str],
        glb_provenance: dict,
        step_lines: list[str],
        target_scope,
        row: dict,
        warnings: list[str],
        thumb_logged: list[bool],
        timings: dict,
        mesh_file: str | None = None,
    ) -> dict[str, str | None]:
        """A written IFC's GLB / STEP / thumbnail (as asked and as possible), then everything
        uploaded beside ``key``. Returns the sidecar's ``derived``. A derived format that fails is
        the row's ``derived_error``, never the file's failure. ``thumb_logged`` is the batch's
        one-element "the no-thumbnail reason is already a warning" flag.

        ``mesh_file``: the quick-look GLB this design file was requested from, when it was (it shares
        this file's stem, so its sidecar is this one from now on)."""
        from ada.assets.build import patch_glb_provenance

        loop = asyncio.get_running_loop()
        derived: dict[str, str | None] = {"glb": None, "step": None, "thumb": None, "mesh": mesh_file}
        local: dict[str, pathlib.Path] = {}
        t = time.perf_counter()
        glb_path = None
        # The thumbnail is rendered from the GLB, so one is made whenever a thumbnail can be.
        if "glb" in also or _thumbnail_unavailable() is None:
            try:
                glb_path = await loop.run_in_executor(None, _convert, ifc_path, "glb", work)
                glb_path.write_bytes(patch_glb_provenance(glb_path.read_bytes(), GlbProvenance(glb_provenance)))
                if "glb" in also:
                    local["glb"] = glb_path
            except Exception as exc:  # noqa: BLE001 - a derived format failing is the file's note
                glb_path = None
                if "glb" in also:
                    row["derived_error"] = f"glb: {exc}"
        if "step" in also:
            try:
                step_path = await loop.run_in_executor(None, _convert, ifc_path, "step", work)
                patch_step_description(step_path, step_lines)
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
            elif not thumb_logged[0]:
                warnings.append(note)
                thumb_logged[0] = True
        elif (reason := _thumbnail_unavailable()) is not None and not thumb_logged[0]:
            warnings.append(reason)
            thumb_logged[0] = True
        timings["thumb_ms"] += _ms(t)

        t = time.perf_counter()
        await ctx.storage.put_path(target_scope, key, ifc_path, content_encoding="gzip")
        for fmt, path in local.items():
            dest = thumb_sidecar_key(key) if fmt == "thumb" else key.rsplit(".", 1)[0] + f".{fmt}"
            await ctx.storage.put_path(target_scope, dest, path, content_encoding="gzip" if fmt == "step" else None)
            derived[fmt] = dest
        if "glb" in local:
            # Also where the viewer looks for the IFC's own conversion, so opening the IFC
            # needs no convert job. Stored gzip-at-rest like every converted GLB.
            from ..converters.keys import derived_key_for

            await ctx.storage.put_path(target_scope, derived_key_for(key, "glb"), local["glb"], content_encoding="gzip")
        timings["upload_ms"] += _ms(t)
        return derived

    async def _export_merged(self, job, ctx, opts, hits, provider, collection, target_scope) -> dict | None:
        """``merge``: the whole batch -- one collection -- as ONE IFC, every hit placed under its
        ancestors in one shared level tree (:func:`wrap_all_in_context`).

        Per hit it is :meth:`_export`'s work up to the model: resolve, read, and a row saying what
        happened (a hit with no geometry, or one that failed to read, is a row and stays out of the
        file). Then one model, one attribute lookup over all of it, one write and one set of derived
        formats -- the GLB through the same converter an uploaded IFC goes through. Every row of a hit
        that made it into the file names the merged file. The sidecar describes the file as the
        collection, with every hit in ``elements``; ``geometry.merged`` says it holds more than one
        node, which is what keeps the viewer from offering a per-node request on it.
        """
        from ada.assets.concepts import part_for_manifest
        from ada.clash.geometry_source import GeometrySourceIndex

        from .. import db as db_module

        loop = asyncio.get_running_loop()
        origin = _origin_of(opts, ctx.scope)
        source = _PrefixFacade(ctx.storage, origin, loop)
        index = GeometrySourceIndex(source)
        geometry_provider = str(opts.get("geometry_provider") or GEOMETRY_PROVIDER)
        include_attrs = bool(opts.get("include_source_attributes", True))
        also = [f for f in (opts.get("also") or []) if f in ("glb", "step")]
        query = opts.get("query") or {}
        run_id = opts.get("run_id")
        scope_key = origin.prefix()
        source_scope = _source_scope_wire(opts.get("source_scope"), origin)
        created_by = opts.get("requested_by")
        timings = {k: 0 for k in ("resolve_ms", "read_ms", "attrs_ms", "ifc_ms", "convert_ms", "thumb_ms", "upload_ms")}
        warnings: list[str] = []
        key = str(hits[0]["file"])

        revision = None
        if ctx.db_pool is not None:
            with contextlib.suppress(Exception):
                for c in await db_module.list_attr_collections(ctx.db_pool, scope=scope_key):
                    if (c["provider"], c["collection"]) == (provider, collection):
                        revision = c.get("revision")
        else:
            warnings.append("this worker has no database: source attributes and matched values were not read")

        rows_out: list[dict] = []
        items: list[tuple[Any, dict]] = []
        read: list[tuple[dict, Any, Any]] = []  # (row, manifest, node) of each hit in the file
        for n, hit in enumerate(hits):
            if await self._cancelled(job, ctx):
                logger.info("worker: %s job %s cancelled after reading %d hit(s)", EXPORT_SEARCH_KIND, job.job_id, n)
                return None
            await ctx.queue.update(job.job_id, stage=f"read {n + 1}/{len(hits)}", progress=0.8 * n / max(1, len(hits)))
            row = {"file": key, "node_id": hit.get("node_id"), "full_name": hit.get("full_name"), "status": "written"}
            rows_out.append(row)
            t = time.perf_counter()
            try:
                manifest, node = await loop.run_in_executor(None, resolve_geometry, index, collection, hit)
            except NoGeometry as exc:
                timings["resolve_ms"] += _ms(t)
                row.update(status="no_geometry", error=str(exc))
                continue
            timings["resolve_ms"] += _ms(t)
            t = time.perf_counter()
            try:
                part = await loop.run_in_executor(
                    None, lambda m=manifest, nd=node: part_for_manifest(m, storage=source, node=nd)
                )
            except Exception as exc:  # noqa: BLE001 - one hit's failure is its row
                logger.warning("worker: %s %s: reading %s failed: %s", EXPORT_SEARCH_KIND, key, hit.get("node_id"), exc)
                row.update(status="error", error=f"{type(exc).__name__}: {exc}")
                continue
            finally:
                timings["read_ms"] += _ms(t)
            items.append((part, hit))
            read.append((row, manifest, node))

        merged = None
        if items:
            await ctx.queue.update(job.job_id, stage=f"write {len(items)} hit(s)", progress=0.8)
            work = pathlib.Path(tempfile.mkdtemp(prefix="ada-search-export-"))
            try:
                model = wrap_all_in_context(items, name=collection)
                t = time.perf_counter()
                attr_rows: list[dict] = []
                if ctx.db_pool is not None:
                    for prov in dict.fromkeys([provider, geometry_provider]):
                        attr_rows.extend(
                            await db_module.lookup_attr_rows(
                                ctx.db_pool,
                                scope=scope_key,
                                provider=prov,
                                collection=collection,
                                node_ids=[e.node_id for e in model.elements if e.node_id],
                                full_names=[nm for e in model.elements for nm in e.lookup_names()],
                                source_refs=[e.source_ref for e in model.elements if e.source_ref],
                            )
                        )
                timings["attrs_ms"] += _ms(t)
                attributes = apply_source_attributes(
                    model,
                    attr_rows,
                    provider=provider,
                    collection=collection,
                    include_attributes=include_attrs,
                    scope=source_scope,
                )
                for (_, hit), element in zip(items, model.hits):
                    stamp_matched(
                        element, matched_attributes(query, (element.row or {}).get("attrs") or hit.get("attrs"))
                    )
                manifests = [m for _, m, _ in read]
                provenance = {
                    "provider": provider,
                    "collection": collection,
                    "source_scope": source_scope,
                    "revision": revision,
                    "geometry_provider": ",".join(sorted({str(m.provider) for m in manifests})),
                    "geometry_subject": ",".join(sorted({str(m.subject) for m in manifests})),
                    "geometry_revision": ",".join(sorted({str(m.revision) for m in manifests})),
                    "merged_hits": len(items),
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
                    matched={},
                    scope=source_scope,
                )

                t = time.perf_counter()
                ifc_path = work / pathlib.PurePosixPath(key).name
                await loop.run_in_executor(None, write_ifc, model, ifc_path)
                timings["ifc_ms"] += _ms(t)
                status_row: dict = {}
                derived = await self._derive_and_upload(
                    ctx,
                    work=work,
                    key=key,
                    ifc_path=ifc_path,
                    also=also,
                    glb_provenance={**provenance, "node_id": None, "full_name": collection},
                    step_lines=[
                        f"ADA export of {len(items)} node(s) of {collection}",
                        f"provider={provider}; collection={collection}",
                        f"revision={revision}; run_id={run_id}",
                    ],
                    target_scope=target_scope,
                    row=status_row,
                    warnings=warnings,
                    thumb_logged=[False],
                    timings=timings,
                )
                # Each hit by the name its part has in the file -- the tree the viewer shows.
                joints = [
                    {
                        "node_id": hit.get("node_id"),
                        "full_name": hit.get("full_name"),
                        "name": str(element.obj.name),
                        "path": hit.get("path") or [],
                        "thumb": None,
                    }
                    for (_, hit), element in zip(items, model.hits)
                ]
                merged_glb = work / f"{ifc_path.stem}.glb"
                if merged_glb.exists() and _thumbnail_unavailable() is None:
                    t = time.perf_counter()
                    cuts_dir = work / "joints"
                    cuts_dir.mkdir(exist_ok=True)
                    try:
                        glbs = await loop.run_in_executor(
                            None, _cut_subtrees, merged_glb, [j["name"] for j in joints], cuts_dir
                        )
                    except Exception as exc:  # noqa: BLE001 - per-hit thumbnails are a nicety
                        glbs = []
                        warnings.append(f"hit thumbnails of {key} skipped ({type(exc).__name__}: {exc})")
                    timings["thumb_ms"] += _ms(t)
                    await self._joint_thumbs(
                        job,
                        ctx,
                        key=key,
                        target_scope=target_scope,
                        joints=joints,
                        glbs=glbs,
                        warnings=warnings,
                        timings=timings,
                    )
                t = time.perf_counter()
                meta = meta_document(
                    file=key,
                    fmt="ifc",
                    created_by=created_by,
                    source={
                        "provider": provider,
                        "scope": source_scope,
                        "collection": collection,
                        "node_id": None,
                        "full_name": collection,
                        "source_ref": None,
                        "revision": revision,
                        "path": [],
                    },
                    geometry={
                        "kind": GEOMETRY_DESIGN,
                        "provider": provenance["geometry_provider"],
                        "subject": provenance["geometry_subject"],
                        "revision": provenance["geometry_revision"],
                        "node": None,
                        "merged": True,
                        "nodes": [str(node) for _, _, node in read],
                    },
                    query=query,
                    matched={},
                    run_id=run_id,
                    attributes=attributes,
                    derived=derived,
                    elements=element_sources(model),
                )
                meta["joints"] = joints
                await ctx.storage.put_bytes(
                    target_scope,
                    meta_sidecar_key(key),
                    json.dumps(meta, indent=1).encode("utf-8"),
                    content_encoding=None,
                )
                timings["upload_ms"] += _ms(t)
                merged = {
                    "file": key,
                    "meta": meta_sidecar_key(key),
                    "thumb": derived["thumb"],
                    "glb": derived["glb"],
                    "step": derived["step"],
                    "hits": len(items),
                    "joints": joints,
                }
                for (row, _, _), joint in zip(read, joints):
                    row.update(meta=meta_sidecar_key(key), thumb=joint["thumb"] or derived["thumb"], merged=True)
                    if status_row.get("derived_error"):
                        row["derived_error"] = status_row["derived_error"]
            except Exception as exc:  # noqa: BLE001 - the merged file failing fails every hit in it
                logger.warning("worker: %s %s failed: %s", EXPORT_SEARCH_KIND, key, exc, exc_info=True)
                for row, _, _ in read:
                    row.update(status="error", error=f"the merged file failed: {type(exc).__name__}: {exc}")
            finally:
                shutil.rmtree(work, ignore_errors=True)
        return _batch_summary(provider, collection, rows_out, timings, warnings, merged=merged)

    async def _export_mesh_merged(self, job, ctx, opts, hits, provider, collection, target_scope) -> dict | None:
        """``geometry: "mesh"`` + ``merge``: every hit's built GLB in ONE GLB per collection.

        The built GLBs are meshopt-packed, which the merge cannot read as they are, so each is
        unpacked (:func:`ada.visit.gltf.meshopt.meshopt_decompress_glb`), its scene hung under a node
        named after the hit -- itself under one node per ancestor, shared, as the IFC merge shares
        its levels -- and the merged scene packed again (:func:`meshopt_compress_glb`) on the way out.
        The names are the ones :func:`merged_mesh_elements` lists, so a picked element in the viewer
        still finds its node in the source tree.
        """
        from ada.assets.build import patch_glb_provenance

        loop = asyncio.get_running_loop()
        origin = _origin_of(opts, ctx.scope)
        query = opts.get("query") or {}
        run_id = opts.get("run_id")
        source_scope = _source_scope_wire(opts.get("source_scope"), origin)
        created_by = opts.get("requested_by")
        timings = {k: 0 for k in ("read_ms", "merge_ms", "thumb_ms", "upload_ms")}
        warnings: list[str] = []
        key = str(hits[0]["file"])
        revision = await self._collection_revision(ctx, provider, collection, origin)

        rows_out: list[dict] = []
        parts: list[tuple[dict, pathlib.Path]] = []
        work = pathlib.Path(tempfile.mkdtemp(prefix="ada-search-export-"))
        merged = None
        try:
            for n, hit in enumerate(hits):
                if await self._cancelled(job, ctx):
                    logger.info(
                        "worker: %s job %s cancelled after reading %d hit(s)", EXPORT_SEARCH_KIND, job.job_id, n
                    )
                    return None
                await ctx.queue.update(
                    job.job_id, stage=f"read {n + 1}/{len(hits)}", progress=0.8 * n / max(1, len(hits))
                )
                row = {
                    "file": key,
                    "node_id": hit.get("node_id"),
                    "full_name": hit.get("full_name"),
                    "status": "written",
                }
                rows_out.append(row)
                mesh = hit.get("mesh") if isinstance(hit.get("mesh"), dict) else None
                if mesh is None or not mesh.get("glb_key"):
                    row.update(
                        status="no_geometry", error=str(hit.get("mesh_error") or f"{provider} published no mesh for it")
                    )
                    continue
                t = time.perf_counter()
                try:
                    # The build ran in the hits' own scope (``routes/asset_exports.build_meshes``).
                    data = await ctx.storage.get_bytes(origin, str(mesh["glb_key"]))
                    if data[:2] == b"\x1f\x8b":
                        import gzip

                        data = gzip.decompress(data)
                    path = work / f"hit-{n}.glb"
                    path.write_bytes(default_black_materials_glb(data))
                    parts.append((hit, path))
                except Exception as exc:  # noqa: BLE001 - one hit's failure is its row
                    row.update(status="error", error=f"{type(exc).__name__}: {exc}")
                finally:
                    timings["read_ms"] += _ms(t)

            if parts:
                await ctx.queue.update(job.job_id, stage=f"merge {len(parts)} mesh(es)", progress=0.8)
                t = time.perf_counter()
                out = work / pathlib.PurePosixPath(key).name
                try:
                    spans = await loop.run_in_executor(None, _merge_glbs, parts, out, collection)
                except Exception as exc:  # noqa: BLE001 - the merged file failing fails every hit in it
                    logger.warning("worker: %s %s failed: %s", EXPORT_SEARCH_KIND, key, exc, exc_info=True)
                    for row in rows_out:
                        if row["status"] == "written":
                            row.update(status="error", error=f"the merged file failed: {type(exc).__name__}: {exc}")
                    parts = []
                timings["merge_ms"] += _ms(t)

            in_file = [hit for hit, _ in parts]
            if in_file:
                provenance = {
                    "provider": provider,
                    "collection": collection,
                    "source_scope": source_scope,
                    "revision": revision,
                    "geometry_kind": GEOMETRY_MESH,
                    "geometry_provider": ",".join(
                        sorted({str(h["mesh"].get("provider") or provider) for h in in_file})
                    ),
                    "geometry_subject": ",".join(sorted({str(h["mesh"].get("subject")) for h in in_file})),
                    "geometry_revision": ",".join(sorted({str(h["mesh"].get("revision")) for h in in_file})),
                    "merged_hits": len(in_file),
                    "query": json.dumps(query, ensure_ascii=False, sort_keys=True),
                    "exported_at": datetime.now(timezone.utc).isoformat(),
                    "exporter": _exporter_version(),
                    "run_id": run_id,
                    "node_id": None,
                    "full_name": collection,
                }
                out.write_bytes(patch_glb_provenance(out.read_bytes(), GlbProvenance(provenance)))

                t = time.perf_counter()
                thumb = work / "thumb.png"
                note = await loop.run_in_executor(None, _render_thumb, out, thumb)
                if note is not None:
                    warnings.append(note)
                timings["thumb_ms"] += _ms(t)
                joints = [
                    {
                        "node_id": hit.get("node_id"),
                        "full_name": hit.get("full_name"),
                        "name": _hit_name(hit),
                        "path": hit.get("path") or [],
                        "ids": span or None,
                        "thumb": None,
                    }
                    for hit, span in zip(in_file, spans)
                ]
                await self._joint_thumbs(
                    job,
                    ctx,
                    key=key,
                    target_scope=target_scope,
                    joints=joints,
                    glbs=[path for _, path in parts],
                    warnings=warnings,
                    timings=timings,
                )

                t = time.perf_counter()
                await ctx.storage.put_path(target_scope, key, out, content_encoding=None)
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
                        "node_id": None,
                        "full_name": collection,
                        "source_ref": None,
                        "revision": revision,
                        "path": [],
                    },
                    geometry={
                        "kind": GEOMETRY_MESH,
                        "provider": provenance["geometry_provider"],
                        "subject": provenance["geometry_subject"],
                        "revision": provenance["geometry_revision"],
                        "node": None,
                        "merged": True,
                        "nodes": [str(h["mesh"].get("node")) for h in in_file],
                    },
                    query=query,
                    matched={},
                    run_id=run_id,
                    attributes={},
                    derived=derived,
                    elements=merged_mesh_elements(
                        in_file, provider=provider, collection=collection, scope=source_scope
                    ),
                )
                meta["joints"] = joints
                await ctx.storage.put_bytes(
                    target_scope,
                    meta_sidecar_key(key),
                    json.dumps(meta, indent=1).encode("utf-8"),
                    content_encoding=None,
                )
                timings["upload_ms"] += _ms(t)
                merged = {
                    "file": key,
                    "meta": meta_sidecar_key(key),
                    "thumb": derived["thumb"],
                    "glb": key,
                    "hits": len(in_file),
                    "joints": joints,
                }
                # The written rows are the hits in the file, in its order.
                for row, joint in zip((r for r in rows_out if r["status"] == "written"), joints):
                    row.update(meta=meta_sidecar_key(key), thumb=joint["thumb"] or derived["thumb"], merged=True)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        summary = _batch_summary(provider, collection, rows_out, timings, warnings, merged=merged)
        summary["geometry"] = GEOMETRY_MESH
        return summary

    async def _joint_thumbs(
        self, job, ctx, *, key: str, target_scope, joints: list[dict], glbs: list, warnings: list[str], timings: dict
    ) -> None:
        """A thumbnail per hit of a merged file, from ``glbs`` (one GLB of just that hit each, or
        None), uploaded as :func:`joint_thumb_key` and named in its ``joints`` entry -- so the
        gallery shows, and steps through, the hits inside the file rather than one picture of all.
        Best effort, like every thumbnail: a render that fails leaves that hit's ``thumb`` None."""
        if _thumbnail_unavailable() is not None:
            return
        loop = asyncio.get_running_loop()
        failed = 0
        for n, (joint, glb) in enumerate(zip(joints, glbs)):
            if glb is None:
                continue
            if n % 25 == 0:
                await ctx.queue.update(job.job_id, stage=f"thumbnails {n + 1}/{len(joints)}", progress=0.9)
            t = time.perf_counter()
            png = pathlib.Path(glb).with_suffix(".thumb.png")
            note = await loop.run_in_executor(None, _render_thumb, pathlib.Path(glb), png)
            timings["thumb_ms"] += _ms(t)
            if note is not None:
                failed += 1
                continue
            t = time.perf_counter()
            dest = joint_thumb_key(key, n)
            await ctx.storage.put_path(target_scope, dest, png, content_encoding=None)
            joint["thumb"] = dest
            timings["upload_ms"] += _ms(t)
        if failed:
            warnings.append(f"{failed} of {len(joints)} hit thumbnail(s) of {key} could not be rendered")

    async def _collection_revision(self, ctx, provider: str, collection: str, origin=None):
        """The indexed revision of (provider, collection) in ``origin`` (default the job's scope),
        or None without a database."""
        if ctx.db_pool is None:
            return None
        from .. import db as db_module

        with contextlib.suppress(Exception):
            for c in await db_module.list_attr_collections(ctx.db_pool, scope=(origin or ctx.scope).prefix()):
                if (c["provider"], c["collection"]) == (provider, collection):
                    return c.get("revision")
        return None

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
        origin = _origin_of(opts, ctx.scope)
        query = opts.get("query") or {}
        run_id = opts.get("run_id")
        source_scope = _source_scope_wire(opts.get("source_scope"), origin)
        created_by = opts.get("requested_by")
        timings = {k: 0 for k in ("read_ms", "thumb_ms", "upload_ms")}
        warnings: list[str] = []
        files: list[dict] = []
        revision = await self._collection_revision(ctx, provider, collection, origin)

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
                row.update(
                    status="no_geometry", error=str(hit.get("mesh_error") or f"{provider} published no mesh for it")
                )
                files.append(row)
                continue
            work = pathlib.Path(tempfile.mkdtemp(prefix="ada-search-export-"))
            try:
                t = time.perf_counter()
                data = await ctx.storage.get_bytes(origin, str(mesh["glb_key"]))
                if data[:2] == b"\x1f\x8b":
                    import gzip

                    data = gzip.decompress(data)
                data = default_black_materials_glb(data)
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
