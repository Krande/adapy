"""Per-element fingerprints of a GLB: what says an element's geometry is still the geometry a link
was made on.

The fingerprint is ``ada.geom_compare.summary``'s: a hash of the element's triangles as drawn, its
positions quantised to 0.01 mm. The same element built the same way hashes the same; any change to
its geometry changes it. Needs numpy (a worker computes them when it annotates a run).
"""

from __future__ import annotations

from typing import Mapping

__all__ = ["element_fingerprints", "keyed_fingerprints"]


def element_fingerprints(data: bytes) -> dict[str, str]:
    """``{GLB element id: fingerprint}`` of every element the GLB draws; empty for a GLB without
    the merged-mesh hierarchy."""
    from ada.geom_compare import glb as _glb
    from ada.geom_compare.summary import summarise_glb

    js, _ = _glb.parse_glb(data)
    hierarchy = _glb.merged_hierarchy(js)
    if hierarchy is None:
        return {}
    ids = {eid: eid for eid in hierarchy.by_id}
    summaries = summarise_glb(data, names=ids, key=lambda name: name)
    return {eid: s.fingerprint for eid, s in summaries.items() if eid in ids}


def keyed_fingerprints(target: Mapping, data: bytes, js: Mapping | None = None) -> dict[str, str]:
    """``{element key: fingerprint}`` of every element of ``target``'s GLB ``data``: the source's
    snapshot (``ada.assoc.records.SourceState``)."""
    from .resolve import glb_json, resolver_for

    resolver = resolver_for(target, js if js is not None else glb_json(data))
    out: dict[str, str] = {}
    for eid, fp in element_fingerprints(data).items():
        try:
            key, _ = resolver.identity(eid)
        except (KeyError, ValueError):
            continue
        out[key] = fp
    return out
