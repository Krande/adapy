"""OCC General-Fuse imprint of beam axes onto plate faces (the OCC backend's home for it).

Relocated out of ``ada.cadit.sat.write.imprint_faces`` so the OpenCASCADE dependency lives
under ``ada.occ`` (the OCC boundary). ``CadBackend.imprint_advanced_faces`` calls this; the SAT
writer reaches it through the backend, never importing OCC directly. See the caller module's
docstring for WHY the pre-split matters (Genie ACIS 21013 relink on un-imprinted curved plates).

Returns plain, backend-neutral data — ``ada.geom`` sub-faces + ``(start, end)`` endpoint pairs —
so nothing OCC crosses back to ``ada.cadit``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ada.config import logger

if TYPE_CHECKING:
    from ada.geom import Point
    from ada.geom.surfaces import AdvancedFace


def _bbox(points) -> tuple:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    zs = [p[2] for p in points]
    return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


def _overlap(a, b, m) -> bool:
    return (
        a[0] <= b[3] + m
        and b[0] <= a[3] + m
        and a[1] <= b[4] + m
        and b[1] <= a[4] + m
        and a[2] <= b[5] + m
        and b[2] <= a[5] + m
    )


def _face_points(af) -> list:
    pts = []
    for bound in af.bounds:
        loop = bound.bound
        for oe in getattr(loop, "edge_list", []):
            pts.append(tuple(float(c) for c in oe.start))
            pts.append(tuple(float(c) for c in oe.end))
    return pts


def imprint_advanced_faces_occ(
    advanced_faces: "list[AdvancedFace]",
    imprint_curves: "list[list[tuple[float, float, float]]]",
    tolerance: float = 1e-6,
) -> "tuple[list[list[AdvancedFace] | None], list[list[tuple[Point, Point]]]]":
    """Split each face in ``advanced_faces`` along the beams touching it and report both the
    sub-faces and the edges each beam became.

    Returns ``(sub_faces, beam_edges)``:
    * ``sub_faces[i]`` = the list of ``ada.geom`` sub-faces plate ``i`` became, or ``None`` when it
      was not imprinted (no beam touched it, or the fuse/conversion failed) — author it monolithically.
    * ``beam_edges[j]`` = ``(start, end)`` endpoint pairs of the face-bounding edges beam ``j`` became.
    """
    from OCC.Core.BOPAlgo import BOPAlgo_Builder
    from OCC.Core.BRep import BRep_Tool
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCC.Core.BRepCheck import BRepCheck_Analyzer
    from OCC.Core.gp import gp_Pnt
    from OCC.Core.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCC.Core.TopExp import topexp
    from OCC.Core.TopoDS import topods
    from OCC.Core.TopTools import TopTools_IndexedDataMapOfShapeListOfShape

    from ada import Point
    from ada.occ.geom.surfaces import make_face_from_geom
    from ada.occ.step.geom.surfaces import occ_face_to_ada_face

    curves = []
    for j, curve in enumerate(imprint_curves or []):
        pts = [tuple(map(float, p)) for p in curve]
        if len(pts) >= 2:
            curves.append((j, pts, _bbox(pts)))
    if not curves:
        return [None] * len(advanced_faces), [[] for _ in (imprint_curves or [])]

    margin = max(tolerance, 1e-6) * 10 + 1e-3
    sub_faces: list = [None] * len(advanced_faces)
    beam_edges: list = [[] for _ in (imprint_curves or [])]
    n_split = n_err = n_invalid = 0

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
        # make_face_from_geom heals invalid faces at source now; a face still
        # BRepCheck-invalid here would hard-segfault the General Fuse, so skip it.
        try:
            if not BRepCheck_Analyzer(occ_face).IsValid():
                n_invalid += 1
                continue
        except Exception:  # noqa: BLE001
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
                if not BRepCheck_Analyzer(sf).IsValid():
                    ok = False
                    break
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
                    k = (
                        round(p0.X(), 6),
                        round(p0.Y(), 6),
                        round(p0.Z(), 6),
                        round(p1.X(), 6),
                        round(p1.Y(), 6),
                        round(p1.Z(), 6),
                    )
                    if k in seen:
                        continue
                    seen.add(k)
                    beam_edges[j].append((Point(p0.X(), p0.Y(), p0.Z()), Point(p1.X(), p1.Y(), p1.Z())))

    if n_err or n_invalid:
        logger.info(f"sat-write: imprint left {n_err} fuse-errored + {n_invalid} BRepCheck-invalid plate(s) monolithic")
    logger.info(f"sat-write: imprint split {n_split} plate(s) along beam axes")
    return sub_faces, beam_edges
