"""Streaming STEP → STEP (AP242) re-export — per-solid, no full Assembly.

Large CAD STEP assemblies (the multi-GB crane) OOM-kill / time out through the
full-OCC ``ada.from_step`` → ``to_stp`` path: OCC builds the whole compound + an
XCAF document in memory before writing. This streams instead — the native NGEOM
reader yields one ``ada.geom.Geometry`` per solid (analytic B-rep incl. B-spline
surfaces/curves and swept surfaces, plus colour + world-placement transforms +
assembly path), and each is hand-authored straight to STEP Part-21 text by
:class:`Ap242StreamWriter`. Peak memory is O(one solid), never the whole model.

No tessellation: the analytic B-rep is preserved end-to-end. A solid whose
geometry uses a surface/curve the writer can't emit kernel-free is counted in
``skipped`` (with the offending type) rather than silently dropped.
"""

from __future__ import annotations

import pathlib
from collections import Counter
from typing import Callable

from ada.config import logger

ProgressFn = Callable[[str, float], None]


def _unsupported_kind(gi) -> str:
    """Best-effort name of the first surface/curve type the writer can't emit,
    for the skip log — so 'no geometry left behind' has a concrete target."""
    import ada.geom.curves as cu
    import ada.geom.surfaces as su

    emittable_surf = (
        su.Plane,
        su.CylindricalSurface,
        su.ConicalSurface,
        su.SphericalSurface,
        su.ToroidalSurface,
        su.BSplineSurfaceWithKnots,
        su.SurfaceOfLinearExtrusion,
        su.SurfaceOfRevolution,
    )
    emittable_curve = (cu.Line, cu.Circle, cu.Ellipse, cu.BSplineCurveWithKnots)
    faces = getattr(gi, "cfs_faces", None) or ([gi] if hasattr(gi, "face_surface") else [])
    for f in faces:
        s = getattr(f, "face_surface", None)
        if s is not None and not isinstance(s, emittable_surf):
            return f"surface:{type(s).__name__}"
        for fb in getattr(f, "bounds", []):
            loop = fb.bound
            for oe in getattr(loop, "edge_list", []):
                ec = getattr(oe, "edge_element", oe)
                eg = getattr(ec, "edge_geometry", None)
                if eg is not None and not isinstance(eg, emittable_curve):
                    return f"curve:{type(eg).__name__}"
    return f"geometry:{type(gi).__name__}"


def stream_step_to_step(
    src_path: str | pathlib.Path,
    out_path: str | pathlib.Path,
    *,
    schema: str = "AP242",
    on_progress: ProgressFn | None = None,
) -> dict:
    """Stream a STEP file to a new AP242 STEP file, one solid at a time.

    Returns stats ``{emitted, skipped, total, instances, total_instances, reasons}``.
    ``emitted``/``skipped``/``total`` count *solids*; ``instances`` is the number of
    placed instances written (one NAUO placement per occurrence — a mapped solid used
    N times counts N) and ``total_instances`` every placed instance seen in the source
    (emitted + skipped). The instance counts are the cross-format parity metric, so a
    caller never has to re-parse the output to count it. ``reasons`` maps the first
    unsupported surface/curve type to its skip count.
    """
    import numpy as np

    from ada.cadit.step.write.ap242_stream import Ap242StreamWriter
    from ada.factories import iter_from_step

    prog = on_progress or (lambda *_: None)
    emitted = skipped = total = 0
    instances = total_instances = 0
    reasons: Counter = Counter()

    prog("streaming-step", 0.1)
    with open(out_path, "w") as fh:
        writer = Ap242StreamWriter(fh, schema=schema, assembly=True)
        with writer:
            # iter_from_step (reader="auto") prefers the fast native reader and
            # transparently falls back to pure-Python when the native NGEOM hydrate
            # can't decode the file.
            for geom in iter_from_step(src_path):
                total += 1
                gi = geom.geometry.geometry if hasattr(geom.geometry, "geometry") else geom.geometry
                color = geom.color.rgb if geom.color is not None else None
                # True instancing: emit the solid's geometry ONCE (local coords), then
                # one placement+NAUO per instance. transforms[k] is the world 4x4 (None
                # => identity); instance_paths[k][:-1] is the nesting breadcrumb.
                mats = geom.transforms if geom.transforms else [None]
                paths = geom.instance_paths if geom.instance_paths else None
                base_name = str(geom.id) if geom.id not in (None, "") else f"solid_{total}"
                inst = []
                for k, m in enumerate(mats):
                    tf = None if m is None else [float(v) for v in np.asarray(m).reshape(-1)]
                    pp = list(paths[k][:-1]) if (paths and k < len(paths) and paths[k]) else None
                    inst.append((tf, pp))
                total_instances += len(inst)
                n = writer.add_solid_instances(gi, name=base_name, color=color, instances=inst)
                if n:
                    emitted += 1
                    instances += n
                else:
                    skipped += 1
                    reasons[_unsupported_kind(gi)] += 1
                if total % 500 == 0:
                    prog(f"streaming-step {total}", 0.1 + 0.8 * min(0.99, total / 10000.0))

    if skipped:
        logger.warning("stream_step_to_step: %d/%d solids skipped (unsupported): %s", skipped, total, dict(reasons))
    prog("ready", 1.0)
    return {
        "emitted": emitted,
        "skipped": skipped,
        "total": total,
        "instances": instances,
        "total_instances": total_instances,
        "reasons": dict(reasons),
    }
