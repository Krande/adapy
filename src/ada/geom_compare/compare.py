"""Compare two geometry sources' tessellations of the same elements -- the whole comparison, given the
GLBs of each side.

THE INPUT is two lists of GLBs in the merged-mesh layout (``glb``), one per source, each with an
optional ``{glb id: name}`` override (a core build maps its entries to published nodes, whose full
names are what two sources share). Elements are joined on ``glb.normalise``'d names.

INCREMENTAL. ``previous`` is the last comparison's per-element state (``{key: {fp_a, fp_b, verdict,
reason, metrics, ...}}``). A pair whose two fingerprints are unchanged keeps its verdict and is not
measured or rendered again; a GLB a caller knows unchanged can be left out by passing its elements'
previous summaries (``known_a`` / ``known_b``) instead of its bytes.

THE OUTPUT is one row per element key (``rows``) and counts per verdict -- everything a caller stores.
Pictures are rendered only for pairs that did not pass, at most ``max_pictures``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping, Optional

from . import glb as _glb
from .classify import (
    FAIL,
    MISSING,
    PASS,
    UNCERTAIN,
    VERDICTS,
    Thresholds,
    classify,
    needs_surfaces,
)
from .distance import surface_distance
from .summary import ElementSummary, summarise_glb

__all__ = ["GlbSource", "CompareResult", "compare_sources"]


@dataclass
class GlbSource:
    """One GLB of one side: its bytes (or a loader), and ``{glb id: name}`` overrides."""

    ref: str
    load: Callable[[], bytes]
    names: Mapping[str, str] = field(default_factory=dict)
    #: Anything the caller wants on each row of an element from this GLB (subject, node ids...).
    meta: Mapping[str, object] = field(default_factory=dict)


@dataclass
class CompareResult:
    rows: list
    counts: dict
    timings: dict
    warnings: list


def _summaries(sources: Iterable[GlbSource], z_up: bool, warnings: list) -> tuple[dict, dict]:
    """``({key: ElementSummary}, {key: source ref})`` over every GLB of a side."""
    out: dict[str, ElementSummary] = {}
    where: dict[str, GlbSource] = {}
    for src in sources:
        try:
            got = summarise_glb(src.load(), names=src.names, z_up=z_up)
        except Exception as exc:  # noqa: BLE001 - one unreadable GLB costs its elements, said so
            warnings.append(f"{src.ref}: not read ({type(exc).__name__}: {exc})")
            continue
        for k, s in got.items():
            prior = out.get(k)
            if prior is not None:
                # One element drawn by two GLBs of a side: widen, add, and hash both.
                s = ElementSummary(
                    k,
                    prior.name,
                    tuple(min(x, y) for x, y in zip(prior.bbox_min, s.bbox_min)),
                    tuple(max(x, y) for x, y in zip(prior.bbox_max, s.bbox_max)),
                    prior.volume + s.volume,
                    prior.area + s.area,
                    prior.triangles + s.triangles,
                    (prior.fingerprint + s.fingerprint)[:24],
                    prior.glb_ids + s.glb_ids,
                )
            out[k] = s
            where[k] = src
    return out, where


def _side(src: Optional[GlbSource], s: Optional[ElementSummary]) -> Optional[dict]:
    """A row's account of one side: the caller's meta for its GLB, and the element's summary."""
    if s is None:
        return None
    meta = dict(src.meta) if src is not None else {}
    names = src.names if src is not None else {}
    nodes = sorted({names.get(i) for i in s.glb_ids if names.get(i)} - {s.name}) if names else []
    return {
        **meta,
        "summary": s.to_dict(),
        **({"glb_ids": s.glb_ids[:20]} if s.glb_ids else {}),
        **({"aliases": nodes} if nodes else {}),
    }


def _mesh(src: Optional[GlbSource], key: str, cache: dict, z_up: bool) -> Optional[_glb.ElementMesh]:
    if src is None:
        return None
    parsed = cache.get(src.ref)
    if parsed is None:
        js, binary = _glb.parse_glb(src.load())
        parsed = cache[src.ref] = (js, binary, _glb.merged_hierarchy(js))
    js, binary, hierarchy = parsed
    if hierarchy is None:
        return None
    got = _glb.element_meshes(js, binary, hierarchy, [key], names=src.names, key=_glb.normalise).get(key)
    if got is None:
        return None
    if z_up:
        p = got.positions
        rot = []
        for i in range(0, len(p), 3):
            rot.extend((p[i], -p[i + 2], p[i + 1]))
        got = _glb.ElementMesh(got.name, rot, got.indices)
    # In mm, like the summaries.
    return _glb.ElementMesh(got.name, [v * 1000.0 for v in got.positions], got.indices)


def compare_sources(
    a: Iterable[GlbSource],
    b: Iterable[GlbSource],
    *,
    thresholds: Thresholds | None = None,
    previous: Mapping[str, Mapping] | None = None,
    max_pictures: int = 500,
    render: Callable | None = None,
    z_up: bool = True,
    max_surfaces: int = 20000,
) -> CompareResult:
    """The comparison of side A against side B (see the module docstring). ``render(mesh_a, mesh_b)``
    returns ``(png_a, png_b, reason)`` (``render.render_pair``); None renders nothing."""
    t = thresholds or Thresholds()
    prev = previous or {}
    warnings: list[str] = []
    t0 = time.monotonic()
    sa, where_a = _summaries(a, z_up, warnings)
    sb, where_b = _summaries(b, z_up, warnings)
    t1 = time.monotonic()
    counts = {v: 0 for v in VERDICTS}
    counts.update({"reused": 0, "measured_surfaces": 0, "pictures": 0})
    caches: tuple[dict, dict] = ({}, {})
    rows: list[dict] = []
    surfaces_left = max_surfaces
    for key in sorted(set(sa) | set(sb)):
        ea, eb = sa.get(key), sb.get(key)
        fa = ea.fingerprint if ea else None
        fb = eb.fingerprint if eb else None
        old = prev.get(key)
        if old and old.get("fp_a") == fa and old.get("fp_b") == fb and old.get("verdict") in VERDICTS:
            verdict, reason, metrics = old["verdict"], old.get("reason") or "", dict(old.get("metrics") or {})
            pictures = {k: old.get(k) for k in ("picture_a", "picture_b") if old.get(k)}
            counts["reused"] += 1
            png_a = png_b = None
        else:
            surfaces = None
            if surfaces_left > 0 and needs_surfaces(ea, eb, t):
                surfaces_left -= 1

                def surfaces(ea=ea, eb=eb, key=key):
                    ma = _mesh(where_a.get(key), key, caches[0], z_up)
                    mb = _mesh(where_b.get(key), key, caches[1], z_up)
                    if ma is None or mb is None:
                        raise LookupError("triangles not found")
                    counts["measured_surfaces"] += 1
                    return surface_distance(ma.positions, ma.indices, mb.positions, mb.indices)

            res = classify(ea, eb, t, surfaces)
            verdict, reason, metrics = res.verdict, res.reason, res.metrics
            pictures = {}
            png_a = png_b = None
            if render is not None and verdict in (FAIL, UNCERTAIN, MISSING) and counts["pictures"] < max_pictures:
                ma = _mesh(where_a.get(key), key, caches[0], z_up) if ea else None
                mb = _mesh(where_b.get(key), key, caches[1], z_up) if eb else None
                png_a, png_b, why = render(ma, mb)
                if why and not warnings.count(f"pictures: {why}"):
                    warnings.append(f"pictures: {why}")
                if png_a or png_b:
                    counts["pictures"] += 1
        counts[verdict] += 1
        row = {
            "key": key,
            "name": (ea or eb).name,
            "verdict": verdict,
            "reason": reason,
            "metrics": metrics,
            "fp_a": fa,
            "fp_b": fb,
            "a": _side(where_a.get(key), ea),
            "b": _side(where_b.get(key), eb),
            **pictures,
        }
        if png_a is not None:
            row["_png_a"] = png_a
        if png_b is not None:
            row["_png_b"] = png_b
        rows.append(row)
    if sa and sb and not any(r["verdict"] != MISSING for r in rows):
        warnings.append(
            "no element name matched on both sides -- a broken join (the two sources name elements differently), "
            "not disagreement"
        )
    if surfaces_left <= 0:
        warnings.append(f"surface comparison capped at {max_surfaces} pairs; the rest stay uncertain")
    timings = {"summaries_ms": int((t1 - t0) * 1000), "compare_ms": int((time.monotonic() - t1) * 1000)}
    counts["compared"] = counts[PASS] + counts[UNCERTAIN] + counts[FAIL]
    return CompareResult(rows, counts, timings, warnings)
