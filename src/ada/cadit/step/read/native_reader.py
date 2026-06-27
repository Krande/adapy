"""Native STEP reader: parse via adacpp's C++ NGEOM reader, hydrate to ada.geom Geometry.

A drop-in for ``stream_read_step`` that uses the OCC-free native parser
(``adacpp.cad.stream_step_to_ngeom``) + ``deserialize_geometries``, yielding the same
``ada.geom.Geometry`` stream — so the import-to-Assembly path (Part/Shape tree) gets the native parse
instead of the pure-Python tokenizer. Geometry, names, colours, world-placement transforms (incl. the
per-representation mixed-unit scaling), and assembly instance paths match the Python stream reader.

The C++ side emits one NGEOM root per solid plus a parallel ``StepRootMeta`` (id / has_color / color /
transforms / instance_paths); this rebuilds each as a ``Geometry``. NOTE ``stream_step_to_ngeom``
currently full-parses the file (not memory-bounded) — fine for small/medium models; large files (the
crane) need the streaming GLB path until the ngeom emitter is made streaming too.
"""

from __future__ import annotations

import pathlib
from typing import Iterator

from ada.geom import Geometry


def native_adacpp_step_available() -> bool:
    """True if adacpp's native STEP->NGEOM entry point is importable."""
    try:
        import adacpp  # noqa: F401

        return hasattr(adacpp.cad, "stream_step_to_ngeom")
    except Exception:
        return False


def native_stream_read_step(step_path: str | pathlib.Path) -> Iterator[Geometry]:
    """Yield one ``ada.geom.Geometry`` per solid, parsed natively — the inverse-serialized B-rep plus
    its colour, world-placement matrices and assembly paths. A drop-in for ``stream_read_step``."""
    import adacpp
    import numpy as np

    from ada.cadit.ngeom.deserialize import deserialize_geometries
    from ada.visit.colors import Color

    nbytes, metas = adacpp.cad.stream_step_to_ngeom(str(step_path))
    geoms = deserialize_geometries(nbytes)
    # geoms and metas are parallel (the C++ emits NGEOM roots in the same order as the meta list).
    for (_rid, geom), meta in zip(geoms, metas):
        ip = meta.instance_paths
        # The solid is named after its owning product (the deepest assembly-path level), matching the
        # Python reader's _solid_name; fall back to the solid's own id when it has no named product.
        product = ip[0][-1][1] if (ip and ip[0]) else None
        gid = product or meta.id
        color = Color(meta.color[0], meta.color[1], meta.color[2]) if meta.has_color else None
        # column-major (glTF) 16-float -> 4x4 (order='F'); a lone identity collapses to no transform,
        # matching the Python reader so single-instance/flat solids stay byte-identical downstream.
        mats = [np.asarray(t, dtype=float).reshape(4, 4, order="F") for t in meta.transforms]
        if len(mats) == 1 and np.allclose(mats[0], np.eye(4), atol=1e-12):
            mats = []
        # The reader couples paths to transforms (the Python reader yields instance_paths only for
        # placed solids); a no-transform solid is a flat child, so drop its path too.
        paths = [tuple(p) for p in ip] if mats else []
        yield Geometry(id=gid, geometry=geom, color=color, transforms=(mats or None), instance_paths=(paths or None))
