"""Imprint beam axes onto plate faces before they are welded into the SAT body.

Genie imprints beams onto plate faces on *import* (a stiffener lying on a panel
splits the panel along its axis). Its own export is already imprinted — a plate
carrying stiffeners arrives as many sub-faces, and every beam carries a
``sat_reference`` naming the edge it lies on, so the importer reuses that edge
instead of re-imprinting. The un-imprinted curved-weld path emits one monolithic
face per plate and no beam edge refs, so Genie re-imprints on import, relinks a
face edge, and raises ``ACIS 21013 - attempt to relink other than vertex or
wire``.

This module pre-splits the faces the same way, for flat *and* curved plates, via
OCC General Fuse (``BOPAlgo_Builder``). It fuses **one plate at a time** against
only the beams whose bounding box meets it: the plate-to-plate sharing is
recovered downstream by the weld (position + curve), so the fuse only has to cut
each plate along its own beams. Per-plate keeps every fuse tiny and robust — a
single ill-conditioned plate falls back to its monolithic face instead of a
whole-model fuse erroring and dropping every imprint. It also reports, per beam,
the edges that beam became so the caller can name them and emit the matching
``sat_reference`` — both halves are needed; pre-split faces alone still relink.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ada.config import logger

if TYPE_CHECKING:
    from ada.geom import Point
    from ada.geom.surfaces import AdvancedFace


@dataclass
class FaceImprint:
    """Result of imprinting plate faces against beam axes.

    ``sub_faces`` is aligned with the input faces: entry ``i`` is the list of
    sub-faces plate ``i`` became, or ``None`` when the plate was not imprinted
    (no beam touched it, or the fuse/conversion failed) — author it monolithically.

    ``beam_edges`` is aligned with the input curves: entry ``j`` is the list of
    ``(start, end)`` endpoint pairs of the face-bounding edges beam ``j`` became.
    """

    sub_faces: "list[list[AdvancedFace] | None]"
    beam_edges: "list[list[tuple[Point, Point]]]" = field(default_factory=list)


def _bbox(points) -> tuple:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    zs = [p[2] for p in points]
    return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


def _overlap(a, b, m) -> bool:
    return (
        a[0] <= b[3] + m and b[0] <= a[3] + m
        and a[1] <= b[4] + m and b[1] <= a[4] + m
        and a[2] <= b[5] + m and b[2] <= a[5] + m
    )


def _face_points(af) -> list:
    pts = []
    for bound in af.bounds:
        loop = bound.bound
        for oe in getattr(loop, "edge_list", []):
            pts.append(tuple(float(c) for c in oe.start))
            pts.append(tuple(float(c) for c in oe.end))
    return pts


def imprint_advanced_faces(
    advanced_faces: "list[AdvancedFace]",
    imprint_curves: "list[list[tuple[float, float, float]]]",
    tolerance: float = 1e-6,
) -> "FaceImprint | None":
    """Split each face in ``advanced_faces`` along the beams touching it and
    report both the sub-faces and the edges each beam became. ``None`` if OCC is
    unavailable (caller keeps the un-imprinted behaviour)."""
    try:
        from OCC.Core.BOPAlgo import BOPAlgo_Builder
        from OCC.Core.BRep import BRep_Tool
        from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
        from OCC.Core.gp import gp_Pnt
        from OCC.Core.TopAbs import TopAbs_EDGE, TopAbs_FACE
        from OCC.Core.TopExp import topexp
        from OCC.Core.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
        from OCC.Core.TopoDS import topods

        from ada import Point
        from ada.occ.geom.surfaces import make_face_from_geom
        from ada.occ.step.geom.surfaces import occ_face_to_ada_face
    except Exception as e:  # noqa: BLE001 - OCC missing (e.g. adacpp-only build)
        logger.warning(f"imprint_advanced_faces: OCC unavailable ({e}); plates left un-imprinted")
        return None

    curves = []
    for j, curve in enumerate(imprint_curves or []):
        pts = [tuple(map(float, p)) for p in curve]
        if len(pts) >= 2:
            curves.append((j, pts, _bbox(pts)))
    if not curves:
        return FaceImprint(sub_faces=[None] * len(advanced_faces), beam_edges=[[] for _ in (imprint_curves or [])])

    margin = max(tolerance, 1e-6) * 10 + 1e-3
    sub_faces: list = [None] * len(advanced_faces)
    beam_edges: list = [[] for _ in (imprint_curves or [])]
    n_split = n_err = 0

    for i, af in enumerate(advanced_faces):
        try:
            fbox = _bbox(_face_points(af))
        except Exception:  # noqa: BLE001
            continue
        near = [c for c in curves if _overlap(fbox, c[2], margin)]
        if not near:
            continue  # no beam touches this plate; author it as-is

        try:
            occ_face = make_face_from_geom(af)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"imprint: could not build OCC face for plate {i}: {e}")
            continue

        curve_cutters: list[tuple[int, list]] = []
        for j, pts, _ in near:
            seg = []
            for a, b in zip(pts, pts[1:]):
                pa, pb = gp_Pnt(*a), gp_Pnt(*b)
                if pa.Distance(pb) > max(tolerance, 1e-12):
                    seg.append(BRepBuilderAPI_MakeEdge(pa, pb).Edge())
            if seg:
                curve_cutters.append((j, seg))
        cutters = [e for _, seg in curve_cutters for e in seg]
        if not cutters:
            continue

        builder = BOPAlgo_Builder()
        builder.AddArgument(occ_face)
        for e in cutters:
            builder.AddArgument(e)
        if tolerance and tolerance > 0:
            builder.SetFuzzyValue(tolerance)
        builder.Perform()
        if builder.HasErrors():
            n_err += 1
            continue
        res = builder.Shape()

        mods = builder.Modified(occ_face)
        if mods is not None and mods.Size() > 0:
            subs = [topods.Face(s) for s in mods]
        elif not builder.IsDeleted(occ_face):
            subs = [occ_face]
        else:
            subs = []
        if len(subs) <= 1:
            # No beam cut this face into pieces (it only crossed at a point, or
            # missed). Leave it monolithic so its authored edge params survive.
            continue

        converted: list = []
        ok = True
        for sf in subs:
            try:
                conv = occ_face_to_ada_face(sf)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"imprint: sub-face conversion raised for plate {i}: {e}")
                conv = None
            if conv is None:
                ok = False
                break
            converted.append(conv)
        if not ok or not converted:
            continue
        sub_faces[i] = converted
        n_split += 1

        edge_faces = TopTools_IndexedDataMapOfShapeListOfShape()
        topexp.MapShapesAndAncestors(res, TopAbs_EDGE, TopAbs_FACE, edge_faces)
        for j, seg in curve_cutters:
            seen = set()
            for cutter in seg:
                cmods = builder.Modified(cutter)
                result_edges = list(cmods) if (cmods is not None and cmods.Size() > 0) else [cutter]
                for re_ in result_edges:
                    if not (edge_faces.Contains(re_) and edge_faces.FindFromKey(re_).Size() > 0):
                        continue
                    e = topods.Edge(re_)
                    v0, v1 = topexp.FirstVertex(e, True), topexp.LastVertex(e, True)
                    p0, p1 = BRep_Tool.Pnt(v0), BRep_Tool.Pnt(v1)
                    k = (round(p0.X(), 6), round(p0.Y(), 6), round(p0.Z(), 6),
                         round(p1.X(), 6), round(p1.Y(), 6), round(p1.Z(), 6))
                    if k in seen:
                        continue
                    seen.add(k)
                    beam_edges[j].append((Point(p0.X(), p0.Y(), p0.Z()), Point(p1.X(), p1.Y(), p1.Z())))

    if n_err:
        logger.info(f"sat-write: imprint skipped {n_err} plate(s) whose fuse errored (left monolithic)")
    logger.info(f"sat-write: imprint split {n_split} plate(s) along beam axes")
    return FaceImprint(sub_faces=sub_faces, beam_edges=beam_edges)
