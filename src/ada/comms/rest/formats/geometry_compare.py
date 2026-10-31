"""Compare two providers' tessellations of one collection (``geometry_compare``).

A synthetic job: its inputs are GLBs already in the job's scope (core builds of each provider's
published subjects, chosen by ``routes/geometry_compare.py``), so there is no source to stream.

``conversion_options``::

    collection, provider_a, provider_b
    sources_a, sources_b   [{glb_key, hierarchy_key?, subject?, node?, revision?}] -- one per GLB
    thresholds             ``ada.geom_compare.Thresholds`` fields (optional)
    max_pictures           pictures rendered at most (failed / uncertain / missing pairs only)
    previous_key           the collection's last state, ``{key: {fp_a, fp_b, verdict, ...}}`` (gz JSON)
    rows_key               where the result rows go (gz NDJSON, one per element key)
    pictures_prefix        where the PNGs go, ``<prefix><key hash>/<fp_a>_<fp_b>-<a|b>.png``

ELEMENT NAMES. A core build stamps ``node_ids`` (glb id -> published node) into its GLB; each such
entry is named by that node's ``full_name`` in the subject's spine (``hierarchy_key``), else its label
-- what two providers publishing the same elements share. An entry with no node id keeps the GLB's
own name.

The answer at ``derived_key``: ``{schema, counts, timings, warnings, rows_key, rows}``.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import pathlib
import tempfile
import time
import traceback as tb_module

from ada.config import logger

from ..queue import JOB_STATUS_DONE, JOB_STATUS_ERROR, Job
from ..worker.audit import _audit_done
from .registry import JobContext, SyntheticFormatHandler

GEOMETRY_COMPARE_KIND = "geometry_compare"
RESULT_SCHEMA = "ada.geom-compare/run@1"


def spine_names(doc: dict) -> dict[str, str]:
    """``{node id: full name | label}`` of a hierarchy slice (``ada.assets/hierarchy@1``)."""
    cols = list(doc.get("cols") or [])
    if "id" not in cols:
        return {}
    at_id = cols.index("id")
    at_full = cols.index("full_name") if "full_name" in cols else None
    at_label = cols.index("label") if "label" in cols else None
    out: dict[str, str] = {}
    for row in doc.get("rows") or ():
        name = (row[at_full] if at_full is not None else None) or (row[at_label] if at_label is not None else None)
        if name:
            out[str(row[at_id])] = str(name)
    return out


def picture_key(prefix: str, key: str, fp_a: str | None, fp_b: str | None, side: str) -> str:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}{digest}/{fp_a or '-'}_{fp_b or '-'}-{side}.png"


async def _json(storage, scope, key: str):
    raw = await storage.get_bytes(scope, key)
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw)


async def _sources(storage, scope, specs: list, tmp: pathlib.Path, side: str, warnings: list):
    """``[GlbSource]`` for one side: each GLB downloaded to ``tmp`` (read lazily from disk by the
    comparison), its entries named through the build's spine."""
    from ada.geom_compare import GlbSource
    from ada.geom_compare.glb import node_ids_of, parse_glb

    out = []
    for i, spec in enumerate(specs or ()):
        key = spec.get("glb_key")
        if not key:
            continue
        try:
            data = await storage.get_bytes(scope, key)
        except Exception as exc:  # noqa: BLE001 - one missing GLB costs its elements, said so
            warnings.append(f"{side}: {key} not readable ({exc})")
            continue
        path = tmp / f"{side}-{i}.glb"
        path.write_bytes(data)
        names: dict[str, str] = {}
        if spec.get("hierarchy_key"):
            try:
                by_node = spine_names(await _json(storage, scope, spec["hierarchy_key"]))
                js, _ = parse_glb(data)
                names = {gid: by_node[node] for gid, node in node_ids_of(js).items() if node in by_node}
            except Exception as exc:  # noqa: BLE001 - the GLB's own names still join
                warnings.append(f"{side}: names of {key} not read from its spine ({exc})")
        del data
        meta = {k: spec[k] for k in ("subject", "node", "revision") if spec.get(k)}
        out.append(GlbSource(ref=key, load=lambda p=path: p.read_bytes(), names=names, meta=meta))
    return out


async def _run(job: Job, ctx: JobContext) -> None:
    from ada.geom_compare import Thresholds, compare_sources
    from ada.geom_compare.render import render_pair

    opts = job.conversion_options or {}
    queue, storage, scope = ctx.queue, ctx.storage, ctx.scope
    t0 = time.monotonic()

    async def fail(stage: str, msg: str, trace: str | None = None) -> None:
        await queue.update(job.job_id, status=JOB_STATUS_ERROR, stage=stage, error=msg)
        await _audit_done(ctx.db_pool, job.job_id, "error", msg, ctx.started_at, traceback=trace)

    missing = [k for k in ("collection", "provider_a", "provider_b", "rows_key", "pictures_prefix") if not opts.get(k)]
    if missing:
        await fail("validate", f"geometry_compare needs {', '.join(missing)}")
        return
    warnings: list[str] = []
    previous = {}
    if opts.get("previous_key"):
        try:
            previous = await _json(storage, scope, opts["previous_key"])
        except Exception as exc:  # noqa: BLE001 - a first comparison, or an unreadable memory: measure all
            warnings.append(f"previous state not read ({exc}); every pair measured")
    try:
        with tempfile.TemporaryDirectory(prefix="geom-compare-") as tmp_dir:
            tmp = pathlib.Path(tmp_dir)
            await queue.update(job.job_id, stage="reading", progress=0.1)
            a = await _sources(storage, scope, opts.get("sources_a"), tmp, "a", warnings)
            b = await _sources(storage, scope, opts.get("sources_b"), tmp, "b", warnings)
            t_read = time.monotonic()
            await queue.update(job.job_id, stage="comparing", progress=0.4)
            result = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: compare_sources(
                    a,
                    b,
                    thresholds=Thresholds.from_options(opts.get("thresholds")),
                    previous=previous,
                    max_pictures=int(opts.get("max_pictures") or 500),
                    render=render_pair,
                ),
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("worker: geometry_compare failed for %s", opts.get("collection"))
        await fail("compare", str(exc), tb_module.format_exc())
        return

    await queue.update(job.job_id, stage="writing", progress=0.85)
    prefix = str(opts["pictures_prefix"])
    lines = []
    for row in result.rows:
        for side in ("a", "b"):
            png = row.pop(f"_png_{side}", None)
            if png is not None:
                pkey = picture_key(prefix, row["key"], row.get("fp_a"), row.get("fp_b"), side)
                await storage.put_bytes(scope, pkey, png)
                row[f"picture_{side}"] = pkey
        lines.append(json.dumps(row, separators=(",", ":")))
    await storage.put_bytes(scope, opts["rows_key"], gzip.compress(("\n".join(lines) + "\n").encode("utf-8")))
    summary = {
        "schema": RESULT_SCHEMA,
        "collection": opts["collection"],
        "provider_a": opts["provider_a"],
        "provider_b": opts["provider_b"],
        "rows_key": opts["rows_key"],
        "rows": len(lines),
        "sources": {"a": len(a), "b": len(b)},
        "counts": result.counts,
        "warnings": (warnings + result.warnings)[:50],
        "timings": {
            **result.timings,
            "read_ms": int((t_read - t0) * 1000),
            "total_ms": int((time.monotonic() - t0) * 1000),
        },
    }
    await storage.put_bytes(scope, job.derived_key, json.dumps(summary).encode("utf-8"))
    await queue.update(job.job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
    await _audit_done(ctx.db_pool, job.job_id, "done", None, ctx.started_at)


class GeometryCompareHandler(SyntheticFormatHandler):
    # Its derived key names the run (``.../runs/<run>/summary.json``), so the cached-blob short
    # circuit never meets an earlier answer.
    kind = GEOMETRY_COMPARE_KIND

    async def run(self, job: Job, ctx: JobContext) -> None:
        await _run(job, ctx)
