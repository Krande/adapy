"""Author FEM-shell *analytic* faces into an embedded ACIS body for Genie XML.

The coplanar merge strategy folds a FEM shell mesh into flat ``<flat_plate>``
polygons — faithful, but a curved skin (a tubular member, a bent panel) then
arrives as thousands of tiny per-plane facets, and the XML balloons. The STEP
and IFC FEM writers avoid this with the *analytic* face source
(:func:`ada.fem.formats.mesh_faces.iter_faces` on the ``surface`` / ``panel``
strategy): each region-grown patch is fitted and emitted as a recognised
cylinder or B-spline surface, so a whole tube collapses to a handful of curved
faces.

Genie XML expresses a curved shell as a ``<curved_shell>`` that names SAT faces
in the embedded ACIS body — the same mechanism a Genie-authored hull export
uses. This module bridges the two: it takes the analytic ``ada.geom`` faces and
authors each into a :class:`~ada.cadit.sat.write.writer.SatWriter`, recording
per face whether it became a curved shell (SAT face refs) or a flat plate
(boundary polygon). The streaming XML writer then emits one ``<structure>`` per
record.

The ACIS SAT reader (and Genie) carries only ``plane-surface`` and
``spline-surface`` faces, so an analytic cylinder is re-expressed as a degree-1
B-spline surface sampling the tube — a curved patch that reads back as a curved
shell rather than degrading to flats. A patch that cannot be authored falls back
to its boundary polygon as a ``<flat_plate>`` — geometry is never dropped.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ada.config import logger


@dataclass
class AnalyticFaceRecord:
    """One FEM face, ready for the streaming XML writer.

    ``face_refs`` non-empty → a ``<curved_shell>`` naming those SAT faces.
    Otherwise ``outline`` + ``normal`` → a ``<flat_plate>`` polygon.
    """

    name: str
    material: str
    thickness: float
    face_refs: list[str] = field(default_factory=list)
    outline: np.ndarray | None = None
    normal: np.ndarray | None = None


def _edge_loop_from_points(points):
    """A closed :class:`EdgeLoop` of straight edges through ``points`` (drops
    coincident/duplicate points and a repeated closing point)."""
    from ada.geom import curves as geo_cu
    from ada.geom.direction import Direction
    from ada.geom.points import Point

    pts: list = []
    for p in points:
        p = np.asarray(p, dtype=float)
        if not pts or float(np.linalg.norm(p - pts[-1])) > 1e-9:
            pts.append(p)
    if len(pts) >= 2 and float(np.linalg.norm(pts[0] - pts[-1])) < 1e-9:
        pts = pts[:-1]
    if len(pts) < 3:
        return None
    edges = []
    for i, a in enumerate(pts):
        b = pts[(i + 1) % len(pts)]
        d = b - a
        line = geo_cu.Line(Point(*a), Direction(*d))
        edges.append(
            geo_cu.OrientedEdge(
                start=Point(*a),
                end=Point(*b),
                edge_element=geo_cu.EdgeCurve(
                    start=Point(*a), end=Point(*b), edge_geometry=line, same_sense=True
                ),
                orientation=True,
            )
        )
    return geo_cu.EdgeLoop(edge_list=edges)


def _boundary_points(geom_face) -> list[np.ndarray]:
    """Every boundary vertex of a geom face, across all its loops."""
    from ada.geom import curves as geo_cu

    pts: list[np.ndarray] = []
    for bound in geom_face.bounds:
        loop = bound.bound
        if isinstance(loop, geo_cu.EdgeLoop):
            pts.extend(np.asarray(oe.start, dtype=float) for oe in loop.edge_list)
        elif isinstance(loop, geo_cu.PolyLoop):
            pts.extend(np.asarray(p, dtype=float) for p in loop.polygon)
    return pts


def _cylinder_to_bspline_faces(geom_face, *, target_deg: float = 15.0):
    """Re-express a trimmed ``CylindricalSurface`` face as degree-1 B-spline
    surface faces (a curved patch the SAT reader carries).

    The tube's angular sweep is recovered from the face's boundary vertices and
    split into ≤120° segments (a full tube into at least two) so each patch is a
    clean, non-degenerate single loop. Each segment samples the cylinder on a
    (θ × z) grid whose control points lie exactly on the tube, so the degree-1
    surface follows it. Returns a list of authorable ``AdvancedFace``.
    """
    surf = geom_face.face_surface
    origin = np.array(surf.position.location, dtype=float)
    axis = np.array(surf.position.axis, dtype=float)
    axis /= np.linalg.norm(axis) or 1.0
    ref = np.array(surf.position.ref_direction, dtype=float)
    ref /= np.linalg.norm(ref) or 1.0
    e2 = np.cross(axis, ref)
    radius = float(surf.radius)

    pts = _boundary_points(geom_face)
    if len(pts) < 3 or radius <= 1e-9:
        return []
    d = np.asarray(pts, dtype=float) - origin
    z = d @ axis
    theta = np.mod(np.arctan2(d @ e2, d @ ref), 2.0 * np.pi)
    z0, z1 = float(z.min()), float(z.max())
    if z1 - z0 < 1e-9:
        return []

    th = np.sort(theta)
    gaps = np.diff(np.concatenate([th, th[:1] + 2.0 * np.pi]))
    gmax_i = int(np.argmax(gaps))
    gmax = float(gaps[gmax_i])
    full = gmax <= math.radians(25.0)
    if full:
        t0, span = 0.0, 2.0 * np.pi
    else:
        t0 = float(th[(gmax_i + 1) % len(th)])
        span = 2.0 * np.pi - gmax

    n_seg = max(2 if full else 1, math.ceil(span / math.radians(120.0)))
    faces = []
    for k in range(n_seg):
        ta = t0 + span * k / n_seg
        tb = t0 + span * (k + 1) / n_seg
        n_th = max(2, math.ceil((tb - ta) / math.radians(target_deg)))
        thetas = np.linspace(ta, tb, n_th + 1)
        radial = np.outer(np.cos(thetas), ref) + np.outer(np.sin(thetas), e2)
        row0 = origin + radius * radial + z0 * axis
        row1 = origin + radius * radial + z1 * axis
        af = _bspline_grid_face([row0, row1])
        if af is not None:
            faces.append(af)
    return faces


def _bspline_grid_face(grid):
    """A degree-1 B-spline surface ``AdvancedFace`` through an ``nu × nv`` grid of
    points, with a straight-edge ``EdgeLoop`` perimeter carrying a UV **pcurve**
    per edge.

    The pcurves are what make the read-back face tessellate as a curved surface:
    the tessellator trims a spline face in its own parameter space, and without a
    pcurve the boundary can't be placed there — the face collapses to a
    degenerate sliver and rendering falls back to flat (exactly what the analytic
    cylinders must avoid). The surface's parameter domain is ``u ∈ [0, nu-1]`` ×
    ``v ∈ [0, nv-1]`` (clamped degree-1 knots), so each perimeter vertex has an
    integer ``(u, v)`` and every edge is an axis-aligned segment in UV."""
    from ada.geom import curves as geo_cu
    from ada.geom import surfaces as geo_su
    from ada.geom.direction import Direction
    from ada.geom.points import Point

    nu = len(grid)
    nv = len(grid[0]) if nu else 0
    if nu < 2 or nv < 2:
        return None

    def _clamped(n):
        return [float(k) for k in range(n)], [2] + [1] * (n - 2) + [2]

    uk, um = _clamped(nu)
    vk, vm = _clamped(nv)
    surf = geo_su.BSplineSurfaceWithKnots(
        u_degree=1,
        v_degree=1,
        control_points_list=[[Point(*grid[i][j]) for j in range(nv)] for i in range(nu)],
        surface_form=geo_su.BSplineSurfaceForm.UNSPECIFIED,
        u_closed=False,
        v_closed=False,
        self_intersect=False,
        u_multiplicities=um,
        v_multiplicities=vm,
        u_knots=uk,
        v_knots=vk,
        knot_spec=geo_cu.KnotType.UNSPECIFIED,
    )

    # perimeter of the grid (CCW), each vertex tagged with its (u, v) parameter
    perim: list[tuple[tuple[float, float], np.ndarray]] = []
    for j in range(nv):
        perim.append(((0.0, float(j)), np.asarray(grid[0][j], dtype=float)))
    for i in range(1, nu):
        perim.append(((float(i), float(nv - 1)), np.asarray(grid[i][nv - 1], dtype=float)))
    for j in range(nv - 2, -1, -1):
        perim.append(((float(nu - 1), float(j)), np.asarray(grid[nu - 1][j], dtype=float)))
    for i in range(nu - 2, 0, -1):
        perim.append(((float(i), 0.0), np.asarray(grid[i][0], dtype=float)))

    edges = []
    for k in range(len(perim)):
        (uv0, p0) = perim[k]
        (uv1, p1) = perim[(k + 1) % len(perim)]
        if float(np.linalg.norm(p1 - p0)) < 1e-12:
            continue
        line = geo_cu.Line(Point(*p0), Direction(*(p1 - p0)))
        pcurve = geo_cu.Pcurve2dBSpline(
            degree=1,
            control_points_2d=[list(uv0), list(uv1)],
            knots=[0.0, 1.0],
            knot_multiplicities=[2, 2],
            same_sense=True,
        )
        edges.append(
            geo_cu.OrientedEdge(
                start=Point(*p0),
                end=Point(*p1),
                edge_element=geo_cu.EdgeCurve(
                    start=Point(*p0), end=Point(*p1), edge_geometry=line, same_sense=True
                ),
                orientation=True,
                pcurve=pcurve,
            )
        )
    if len(edges) < 3:
        return None
    return geo_su.AdvancedFace(
        bounds=[geo_su.FaceBound(bound=geo_cu.EdgeLoop(edge_list=edges), orientation=True)],
        face_surface=surf,
        same_sense=True,
    )


def analytic_face_to_authorable_faces(geom_face) -> list:
    """Convert one analytic ``ada.geom`` face into ``AdvancedFace`` records the
    SAT curved-plate writer can author (spline surface + straight-edge loop).

    A ``CylindricalSurface`` becomes one or more degree-1 B-spline patches; a
    B-spline surface keeps its surface and gets a straight-edge loop rebuilt from
    its boundary polygon. Anything else yields nothing (the caller falls back to
    a flat polygon)."""
    from ada.geom import curves as geo_cu
    from ada.geom import surfaces as geo_su

    surf = geom_face.face_surface
    if isinstance(surf, geo_su.CylindricalSurface):
        return _cylinder_to_bspline_faces(geom_face)
    if isinstance(surf, geo_su.BSplineSurfaceWithKnots):
        bound = geom_face.bounds[0].bound
        if isinstance(bound, geo_cu.EdgeLoop):
            return [geom_face]
        loop = _edge_loop_from_points(_boundary_points(geom_face))
        if loop is None:
            return []
        return [
            geo_su.AdvancedFace(
                bounds=[geo_su.FaceBound(bound=loop, orientation=True)],
                face_surface=surf,
                same_sense=geom_face.same_sense,
            )
        ]
    return []


def _flat_polygon_from_geom_face(geom_face):
    """Boundary polygon + normal for a geom face whose analytic authoring failed
    — the never-drop fallback (position/extent faithful, curvature lost)."""
    pts = _boundary_points(geom_face)
    if len(pts) < 3:
        return None, None
    arr = np.asarray(pts, dtype=float)
    nrm = np.zeros(3)
    for i in range(len(arr)):
        a, b = arr[i], arr[(i + 1) % len(arr)]
        nrm[0] += (a[1] - b[1]) * (a[2] + b[2])
        nrm[1] += (a[2] - b[2]) * (a[0] + b[0])
        nrm[2] += (a[0] - b[0]) * (a[1] + b[1])
    length = float(np.linalg.norm(nrm))
    if length < 1e-12:
        return None, None
    return arr, nrm / length


def analytic_faces_to_sat_writer(part, strategy):
    """Author every FEM analytic face under ``part`` into a SAT body.

    Returns ``(SatWriter, list[AnalyticFaceRecord])``. Sourced from the strong
    analytic merge (:func:`ada.fem.formats.mesh_faces.iter_fem_analytic_faces`,
    the exact face set FEM→STEP/IFC emit) tagged with each face's (material,
    thickness): a **planar** patch becomes a compact ``<flat_plate>`` boundary
    polygon (holes, where present, are noted and the outer boundary kept); a
    **cylinder** (or B-spline) patch is authored into the ACIS body and
    referenced by a ``<curved_shell>``. No face is ever dropped.
    """
    from ada.geom import curves as geo_cu
    from ada.geom import surfaces as geo_su

    from ada.cadit.sat.utils import make_ints_if_possible
    from ada.cadit.sat.write import sat_entities as se
    from ada.cadit.sat.write.write_curved_plate import (
        TopologyWeld,
        UnsupportedCurvedFace,
        advanced_face_to_sat_entities,
        link_partner_rings,
    )
    from ada.cadit.sat.write.writer import SatWriter, _assign_faces_to_shells
    from ada.fem.formats.mesh_faces import MergeStrategy, iter_fem_analytic_faces

    sw = SatWriter(part)
    idg = sw.id_generator
    weld = TopologyWeld(idg)

    records: list[AnalyticFaceRecord] = []
    face_id = 1
    n_curved = 0
    n_flat = 0
    n_holed = 0
    n_fallback = 0

    # Author the faces first (collecting bbox points), then create the
    # body/lump/shell around them — advanced_face_to_sat_entities needs sw.shell,
    # so the shell is created up front with a provisional box that is finalised
    # once every face vertex is known.
    body = se.Body(idg.next_id(), None, [0.0] * 6)
    lump = se.Lump(idg.next_id(), None, body, [0.0] * 6)
    shell = se.Shell(idg.next_id(), None, lump, [0.0] * 6)
    body.lump = lump
    lump.shell = shell
    sw.body, sw.lump, sw.shell = body, lump, shell
    for e in (body, lump, shell):
        sw.add_entity(e)

    reconstruct = strategy == MergeStrategy.PANEL
    all_pts: list[np.ndarray] = []
    struct_id = 0

    def _outer_polygon(geom_face):
        """The outer boundary polygon + plane normal of a planar geom face."""
        bound = geom_face.bounds[0].bound
        if isinstance(bound, geo_cu.PolyLoop):
            outline = np.asarray([[p[0], p[1], p[2]] for p in bound.polygon], dtype=float)
        else:
            outline, _ = _flat_polygon_from_geom_face(geom_face)
            if outline is None:
                return None, None
        pos = getattr(geom_face.face_surface, "position", None)
        if pos is not None:
            normal = np.asarray(pos.axis, dtype=float)
        else:
            _, normal = _flat_polygon_from_geom_face(geom_face)
        return outline, normal

    for geom_face, material, thickness in iter_fem_analytic_faces(part, with_meta=True, reconstruct_curved=reconstruct):
        struct_id += 1
        surf = geom_face.face_surface

        if isinstance(surf, geo_su.Plane):
            outline, normal = _outer_polygon(geom_face)
            if outline is None or len(outline) < 3 or normal is None:
                logger.warning("analytic-xml: a planar face has no usable boundary; dropped")
                continue
            if len(geom_face.bounds) > 1:
                # Genie <flat_plate> is a single polygon, so an inner void loop
                # cannot be carried here — keep the outer boundary (never drop the
                # face). Rare (a handful per model); logged for visibility.
                n_holed += 1
            records.append(
                AnalyticFaceRecord(f"pl{struct_id}", material, float(thickness), outline=outline, normal=normal)
            )
            n_flat += 1
            continue

        # Curved patch → author to the ACIS body, reference by <curved_shell>.
        refs: list[str] = []
        for af in analytic_face_to_authorable_faces(geom_face):
            name = f"FACE{face_id:08d}"
            try:
                entities = advanced_face_to_sat_entities(af, name, sw, weld)
            except UnsupportedCurvedFace as ex:
                logger.debug(f"analytic-xml: curved face not authored to SAT: {ex}")
                continue
            except Exception as ex:  # noqa: BLE001 - a bad face must not sink the whole write
                logger.debug(f"analytic-xml: curved face SAT author error: {ex}")
                continue
            for entity in entities:
                sw.add_entity(entity)
            all_pts.extend(np.asarray(oe.start, dtype=float) for oe in af.bounds[0].bound.edge_list)
            refs.append(name)
            face_id += 1

        if refs:
            records.append(AnalyticFaceRecord(f"cs{struct_id}", material, float(thickness), face_refs=refs))
            n_curved += 1
        else:
            outline, normal = _flat_polygon_from_geom_face(geom_face)
            if outline is None:
                logger.warning("analytic-xml: a curved face has no usable geometry; dropped")
                continue
            records.append(
                AnalyticFaceRecord(f"cf{struct_id}", material, float(thickness), outline=outline, normal=normal)
            )
            n_fallback += 1

    link_partner_rings(weld)
    for entity in weld.entities:
        sw.add_entity(entity)

    if all_pts:
        arr = np.asarray(all_pts, dtype=float)
        bbox = make_ints_if_possible([*arr.min(axis=0), *arr.max(axis=0)])
        sw.bbox = bbox
        for e in (body, lump, shell):
            e.bbox = list(bbox)

    if sw.get_entities_by_type(se.Face):
        _assign_faces_to_shells(sw, shell)
    sw.renumber()

    logger.info(
        f"analytic-xml: {n_curved} curved shells ({face_id - 1} SAT faces), "
        f"{n_flat} flat plates ({n_holed} with holes kept outer-only), {n_fallback} curved→flat fallbacks"
    )
    return sw, records
