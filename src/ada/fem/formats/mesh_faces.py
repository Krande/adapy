"""Object-free, vectorized FEM-shell → CAD-face source.

The FEM→CAD writers historically rebuilt one ``Plate`` per shell element and
then merged those objects (``concept_merge``). That materialises tens of
thousands of objects just to throw most away — the dominant memory/time cost of
FEM→CAD export.

This module produces the *merged faces* directly from the array-backed mesh
(``MeshArrays``: ``coords`` + per-block ``conn`` row-indices), with **no Plate
or Elem objects built**. Each strategy is a vectorized function over the mesh
arrays that yields lightweight :class:`FaceData` records (raw outline + refs);
a streaming writer turns those into ``<flat_plate>`` text one at a time.

Strategies (``MergeStrategy``):

* ``NONE``     — identity: one face per shell element (non-coplanar quads split
  into two triangles, mirroring the legacy 1:1 conversion).
* ``COPLANAR`` — edge-adjacent coplanar shells sharing material+thickness merged
  into their union polygon. The *grouping* (plane buckets + edge-connected
  components) is computed here vectorized; the union itself reuses the tested
  :func:`merge_coplanar_loops_by_edge_cancellation` core on raw point loops, so
  the result is geometrically equivalent to the object-based merge.

``SURFACE`` (curved-panel reconstruction) and ``PANEL`` (structured-quad region
growing) slot in behind the same :func:`iter_faces` interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterator

import numpy as np

from ada.config import logger


class MergeStrategy(str, Enum):
    NONE = "none"
    COPLANAR = "coplanar"
    PLANAR = "planar"
    SURFACE = "surface"
    PANEL = "panel"

    @classmethod
    def from_value(cls, value) -> "MergeStrategy":
        if isinstance(value, cls):
            return value
        if value is None:
            return cls.NONE
        if isinstance(value, bool):  # legacy merge=True/False
            return cls.COPLANAR if value else cls.NONE
        return cls(str(value).lower())


@dataclass
class FaceData:
    """A single planar CAD face, as raw data (no Plate object)."""

    name: str
    outline: np.ndarray  # (k, 3) global coordinates
    normal: np.ndarray  # (3,) unit normal
    material: str
    thickness: float


# ── substrate ───────────────────────────────────────────────────────────────


@dataclass
class _ShellBlock:
    coords: np.ndarray  # (N, 3) the shared node coords
    conn: np.ndarray  # (M, k) row-indices into coords
    el_ids: np.ndarray  # (M,)
    materials: list  # length-M material names
    thicknesses: np.ndarray  # (M,) float


def _ensure_array_backed(fem):
    from ada.api.mesh.containers import ArrayElements, to_array_backed

    if not isinstance(fem.elements, ArrayElements):
        to_array_backed(fem)
    return fem.elements._store


def _shell_blocks(fem) -> Iterator[_ShellBlock]:
    """Yield one :class:`_ShellBlock` per shell element block, with per-element
    material name + thickness resolved from the block's ``fem_secs``."""
    from ada.fem.shapes.definitions import ShellShapes

    store = _ensure_array_backed(fem)
    coords = store.coords
    for ctype, blk in store.blocks.items():
        if not isinstance(ctype, ShellShapes):
            continue
        m = blk.conn.shape[0]
        fem_secs = blk.fem_secs
        materials: list = [None] * m
        thicknesses = np.zeros(m, dtype=np.float64)
        usable = np.zeros(m, dtype=bool)
        if fem_secs is not None:
            for i, fs in enumerate(fem_secs):
                # No section, or a solid/thickness-less section (2D continuum
                # elements) -> not a plate; skip (mirrors convert_shell_elem).
                t = getattr(fs, "thickness", None) if fs is not None else None
                mat = getattr(fs, "material", None) if fs is not None else None
                if t is None or mat is None:
                    continue
                materials[i] = mat.name
                thicknesses[i] = float(t)
                usable[i] = True
        if not usable.any():
            continue
        rows = np.nonzero(usable)[0]
        yield _ShellBlock(
            coords=coords,
            conn=blk.conn[rows],
            el_ids=blk.el_ids[rows],
            materials=[materials[r] for r in rows],
            thicknesses=thicknesses[rows],
        )


def _newell_normals(pts: np.ndarray) -> np.ndarray:
    """Vectorized Newell normal for a batch of polygons ``pts`` (M, k, 3)."""
    p = pts
    pn = np.roll(p, -1, axis=1)
    nx = np.sum((p[:, :, 1] - pn[:, :, 1]) * (p[:, :, 2] + pn[:, :, 2]), axis=1)
    ny = np.sum((p[:, :, 2] - pn[:, :, 2]) * (p[:, :, 0] + pn[:, :, 0]), axis=1)
    nz = np.sum((p[:, :, 0] - pn[:, :, 0]) * (p[:, :, 1] + pn[:, :, 1]), axis=1)
    n = np.stack([nx, ny, nz], axis=1)
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    ln[ln == 0] = 1.0
    return n / ln


def _canonical_sign(normals: np.ndarray, tol: float) -> np.ndarray:
    """+1/-1 per row so a face and its flip share a plane bucket (first
    significant component made positive)."""
    sign = np.ones(normals.shape[0], dtype=np.float64)
    chosen = np.zeros(normals.shape[0], dtype=bool)
    for c in range(3):
        comp = normals[:, c]
        take = (~chosen) & (np.abs(comp) > tol)
        sign[take] = np.sign(comp[take])
        chosen |= take
    sign[sign == 0] = 1.0
    return sign


# ── strategies ──────────────────────────────────────────────────────────────


def _is_coplanar_quad(pts: np.ndarray) -> np.ndarray:
    """(M,) bool: vectorized, bit-for-bit replica of ``vector_utils.is_coplanar``
    for a batch of quads ``pts`` (M,4,3).

    The legacy quad-split decision (``convert_shell_elem_to_plates``) uses
    ``is_coplanar``'s *exact* ``== 0`` plane test — not a tolerance — so on a
    curved mesh nearly every warped quad splits into two triangles. To produce
    the same primitive decomposition we must match that exactly. The arithmetic
    and operand order mirror the scalar implementation; IEEE-symmetric negation
    makes the plane-offset association bit-identical."""
    p0, p1, p2, p3 = pts[:, 0], pts[:, 1], pts[:, 2], pts[:, 3]
    v1 = p1 - p0
    v2 = p2 - p0
    a = v1[:, 1] * v2[:, 2] - v2[:, 1] * v1[:, 2]
    b = v2[:, 0] * v1[:, 2] - v1[:, 0] * v2[:, 2]
    c = v1[:, 0] * v2[:, 1] - v1[:, 1] * v2[:, 0]
    neg_d = a * p0[:, 0] + b * p0[:, 1] + c * p0[:, 2]
    return a * p3[:, 0] + b * p3[:, 1] + c * p3[:, 2] - neg_d == 0


# ── primitives ──────────────────────────────────────────────────────────────
# The merge operates on *primitive faces*, mirroring the legacy conversion: a
# coplanar quad or a triangle is one primitive; a non-coplanar (warped) quad is
# split into two triangles. Both NONE and COPLANAR build the same primitive set,
# so they agree on geometry — COPLANAR just additionally folds edge-adjacent
# coplanar primitives together.


class _Primitives:
    """Per-block primitive faces, as node-row tuples into the shared coords."""

    def __init__(self, coords, rows, names, mats, ts, normals):
        self.coords = coords
        self.rows = rows  # list[tuple[int, ...]] of node row-indices
        self.names = names
        self.mats = mats
        self.ts = ts
        self.normals = normals  # (P, 3)

    def __len__(self):
        return len(self.rows)

    def outline(self, j) -> np.ndarray:
        return self.coords[list(self.rows[j])]

    def face(self, j, name=None) -> FaceData:
        return FaceData(name or self.names[j], self.outline(j), self.normals[j], self.mats[j], self.ts[j])


def _block_primitives(blk: _ShellBlock) -> _Primitives:
    conn = blk.conn
    coords = blk.coords
    k = conn.shape[1]
    planar = _is_coplanar_quad(coords[conn]) if k == 4 else np.ones(conn.shape[0], dtype=bool)

    rows: list[tuple] = []
    names: list[str] = []
    mats: list = []
    ts: list[float] = []
    for i in range(conn.shape[0]):
        eid = int(blk.el_ids[i])
        mat = blk.materials[i]
        t = float(blk.thicknesses[i])
        ring = conn[i]
        if k == 4 and not planar[i]:
            # warped quad -> two triangles (matches convert_shell_elem_to_plates)
            rows.append((int(ring[0]), int(ring[1]), int(ring[2])))
            names.append(f"sh{eid}")
            mats.append(mat)
            ts.append(t)
            rows.append((int(ring[0]), int(ring[2]), int(ring[3])))
            names.append(f"sh{eid}_1")
            mats.append(mat)
            ts.append(t)
        else:
            rows.append(tuple(int(r) for r in ring))
            names.append(f"sh{eid}")
            mats.append(mat)
            ts.append(t)

    # batched Newell normals, grouped by vertex count (tri vs quad)
    normals = np.zeros((len(rows), 3), dtype=np.float64)
    by_len: dict[int, list[int]] = {}
    for j, r in enumerate(rows):
        by_len.setdefault(len(r), []).append(j)
    for length, idxs in by_len.items():
        batch = coords[np.array([rows[j] for j in idxs], dtype=np.int64)]  # (G, length, 3)
        nb = _newell_normals(batch)
        normals[np.array(idxs)] = nb

    return _Primitives(coords, rows, names, mats, ts, normals)


# ── strategies ──────────────────────────────────────────────────────────────


# ── region growing (shared by the planar/surface strategies + the preview) ────


def _local_adjacency(prims: "_Primitives", idxs: list[int]) -> list[list[int]]:
    """Shared-edge adjacency among a subset of primitives (conformal mesh: a shared
    edge == shared node rows). Returns per-local-index neighbour lists."""
    adj: list[list[int]] = [[] for _ in idxs]
    edge_owner: dict = {}
    for li, j in enumerate(idxs):
        r = prims.rows[j]
        k = len(r)
        for e in range(k):
            a, b = r[e], r[(e + 1) % k]
            ek = (a, b) if a <= b else (b, a)
            edge_owner.setdefault(ek, []).append(li)
    for owners in edge_owner.values():
        for x in range(len(owners)):
            for y in range(x + 1, len(owners)):
                adj[owners[x]].append(owners[y])
                adj[owners[y]].append(owners[x])
    return adj


def _material_thickness_groups(prims: "_Primitives", ndigits: int) -> list[list[int]]:
    """Primitive indices bucketed by (material, thickness) — the coarsest grouping a
    single plate can span (different material/thickness can't share a face)."""
    thick_q = np.round(np.array(prims.ts), ndigits)
    mt: dict = {}
    for j in range(len(prims)):
        mt.setdefault((prims.mats[j], float(thick_q[j])), []).append(j)
    return list(mt.values())


def _surface_patches(prims: "_Primitives", angle_tol: float, ndigits: int) -> list[list[int]]:
    """Region-grow *smooth* patches by normal continuity — the curved-skin analogue of
    coplanar bucketing. Per (material, thickness), flood over shared-edge adjacency
    joining two edge-adjacent primitives when the angle between their normals is
    ``<= angle_tol``. Curvature accumulates across the patch (a cylinder's normals
    sweep 360° yet every adjacent pair is within tol); a sharp feature edge stops it.
    No rectangular-grid requirement, so irregular/triangulated skin grows into one big
    patch. Returns list[list[prim_idx]]."""
    import math

    cos_tol = math.cos(math.radians(angle_tol))
    normals = prims.normals
    patches: list[list[int]] = []
    for idxs in _material_thickness_groups(prims, ndigits):
        if len(idxs) == 1:
            patches.append([idxs[0]])
            continue
        adj = _local_adjacency(prims, idxs)
        visited = [False] * len(idxs)
        for s in range(len(idxs)):
            if visited[s]:
                continue
            comp: list[int] = []
            stack = [s]
            visited[s] = True
            while stack:
                cur = stack.pop()
                comp.append(idxs[cur])
                ncur = normals[idxs[cur]]
                for nb in adj[cur]:
                    if visited[nb]:
                        continue
                    if abs(float(np.dot(ncur, normals[idxs[nb]]))) >= cos_tol:
                        visited[nb] = True
                        stack.append(nb)
            patches.append(comp)
    return patches


def _planar_patches(prims: "_Primitives", max_dev: float, ndigits: int) -> list[list[int]]:
    """Region-grow *flat* patches: a facet joins the patch only while every one of its
    vertices stays within ``max_dev`` of the patch seed's plane. Flat-by-construction,
    so each patch emits as one flat plate; a curved region naturally breaks into
    several flat patches (piecewise, bounded error) rather than over-merging a flat
    panel into an adjacent curve. Returns list[list[prim_idx]]."""
    normals = prims.normals
    patches: list[list[int]] = []
    for idxs in _material_thickness_groups(prims, ndigits):
        if len(idxs) == 1:
            patches.append([idxs[0]])
            continue
        adj = _local_adjacency(prims, idxs)
        visited = [False] * len(idxs)
        for s in range(len(idxs)):
            if visited[s]:
                continue
            # seed plane: the seed facet's normal + centroid; grow neighbours whose
            # vertices all lie within max_dev of THIS plane (so the whole patch is
            # provably within max_dev of one plane → a faithful single flat plate).
            seed = idxs[s]
            n0 = normals[seed]
            p0 = prims.outline(seed).mean(axis=0)
            comp = []
            stack = [s]
            visited[s] = True
            while stack:
                cur = stack.pop()
                comp.append(idxs[cur])
                for nb in adj[cur]:
                    if visited[nb]:
                        continue
                    verts = prims.outline(idxs[nb])
                    if float(np.abs((verts - p0) @ n0).max()) <= max_dev:
                        visited[nb] = True
                        stack.append(nb)
            patches.append(comp)
    return patches


def _auto_max_dev(prims: "_Primitives") -> float:
    """Default planar-growing tolerance: 1e-3 of the block's bounding diagonal — tight
    enough to keep 'flat' meaningful, loose enough to absorb FEM node jitter."""
    used = np.unique(np.concatenate([np.asarray(r) for r in prims.rows]))
    pts = prims.coords[used]
    diag = float(np.linalg.norm(pts.max(axis=0) - pts.min(axis=0)))
    return 1e-3 * max(diag, 1e-9)


# ── analytic primitive fits (patch recognition) ───────────────────────────────
# A region-grown smooth patch is classified by fitting analytic surfaces to its
# facet vertices + normals: a jacket member is a CYLINDER (all facet normals are
# radial ⟂ the axis), a ship panel is PLANAR, the rest is FREEFORM (→ B-spline).


def _fit_plane(pts: np.ndarray) -> tuple[np.ndarray, float]:
    """Best-fit plane; returns (unit normal, max deviation / bbox diagonal)."""
    c = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - c, full_matrices=False)
    n = vt[-1]
    dev = float(np.abs((pts - c) @ n).max())
    diag = float(np.linalg.norm(pts.max(axis=0) - pts.min(axis=0)))
    return n, (dev / diag if diag > 1e-12 else 0.0)


def _fit_cylinder(pts: np.ndarray, normals: np.ndarray) -> tuple[float, float, float]:
    """Fit a cylinder: the axis is the direction least aligned with the (radial) facet
    normals; a Kåsa circle fit in the cross-section gives the radius. Returns
    (radius, max radial residual / radius, axial span / radius)."""
    nn = normals / (np.linalg.norm(normals, axis=1, keepdims=True) + 1e-12)
    _, _, vt = np.linalg.svd(nn - nn.mean(axis=0), full_matrices=False)
    axis = vt[-1]
    axis = axis / (np.linalg.norm(axis) + 1e-12)
    tmp = np.array([1.0, 0.0, 0.0]) if abs(axis[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    e1 = np.cross(axis, tmp)
    e1 /= np.linalg.norm(e1) + 1e-12
    e2 = np.cross(axis, e1)
    c = pts.mean(axis=0)
    u = (pts - c) @ e1
    v = (pts - c) @ e2
    sol, *_ = np.linalg.lstsq(np.column_stack([u, v, np.ones_like(u)]), u * u + v * v, rcond=None)
    cu, cv = sol[0] / 2.0, sol[1] / 2.0
    r = float(np.sqrt(max(sol[2] + cu * cu + cv * cv, 0.0)))
    rad = np.sqrt((u - cu) ** 2 + (v - cv) ** 2)
    rel = float(np.abs(rad - r).max() / r) if r > 1e-9 else 1e9
    span = float(np.ptp((pts - c) @ axis))
    return r, rel, (span / r if r > 1e-9 else 0.0)


@dataclass
class CylinderFit:
    """A fitted cylinder + the patch's trim extent in the surface's own (θ, z)
    parameters — everything the STEP/IFC CYLINDRICAL_SURFACE emit needs.

    ``origin`` is a point on the axis; ``(e1, e2, axis)`` is an orthonormal frame, so a
    3D point maps to ``z = (p-origin)·axis``, ``θ = atan2((p-origin)·e2, (p-origin)·e1)``
    and lies on the surface at radius ``radius``. ``z0/z1`` bound the tube axially;
    ``theta_min/theta_max`` its angular sweep (``full360`` when it wraps a closed tube)."""

    origin: np.ndarray
    axis: np.ndarray
    radius: float
    e1: np.ndarray
    e2: np.ndarray
    z0: float
    z1: float
    theta_min: float
    theta_max: float
    full360: bool
    max_rel_resid: float


def fit_cylinder_params(prims: "_Primitives", patch: list[int], *, gap_tol_deg: float = 20.0) -> "CylinderFit | None":
    """Full cylinder fit for a patch: axis (⟂ the radial facet normals), radius (circle
    fit), and the axial + angular trim extent in the surface's own parameters. Returns
    None if the patch isn't a usable cylinder. ``gap_tol_deg`` is the largest angular gap
    still treated as a closed 360° tube (so a full tube with a meshing seam reads full)."""
    pts = np.vstack([prims.outline(j) for j in patch])
    normals = np.array([prims.normals[j] for j in patch], dtype=float)
    nn = normals / (np.linalg.norm(normals, axis=1, keepdims=True) + 1e-12)
    _, _, vt = np.linalg.svd(nn - nn.mean(axis=0), full_matrices=False)
    axis = vt[-1]
    axis = axis / (np.linalg.norm(axis) + 1e-12)
    tmp = np.array([1.0, 0.0, 0.0]) if abs(axis[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    e1 = np.cross(axis, tmp)
    e1 /= np.linalg.norm(e1) + 1e-12
    e2 = np.cross(axis, e1)
    c = pts.mean(axis=0)
    u = (pts - c) @ e1
    v = (pts - c) @ e2
    sol, *_ = np.linalg.lstsq(np.column_stack([u, v, np.ones_like(u)]), u * u + v * v, rcond=None)
    cu, cv = sol[0] / 2.0, sol[1] / 2.0
    r = float(np.sqrt(max(sol[2] + cu * cu + cv * cv, 0.0)))
    if r <= 1e-9:
        return None
    origin = c + cu * e1 + cv * e2  # a point on the axis
    d = pts - origin
    z = d @ axis
    uu = d @ e1
    vv = d @ e2
    rad = np.sqrt(uu * uu + vv * vv)
    max_rel = float(np.abs(rad - r).max() / r)
    theta = np.mod(np.arctan2(vv, uu), 2.0 * np.pi)
    th = np.sort(theta)
    gaps = np.diff(np.concatenate([th, th[:1] + 2.0 * np.pi]))
    gmax_i = int(np.argmax(gaps))
    gmax = float(gaps[gmax_i])
    full360 = bool(gmax <= np.radians(gap_tol_deg))
    if full360:
        theta_min, theta_max = 0.0, 2.0 * np.pi
    else:
        # the arc is the complement of the largest gap: it runs from the vertex after the
        # gap, around, to the vertex before it.
        theta_min = float(th[(gmax_i + 1) % len(th)])
        theta_max = theta_min + (2.0 * np.pi - gmax)
    return CylinderFit(
        origin=origin,
        axis=axis,
        radius=r,
        e1=e1,
        e2=e2,
        z0=float(z.min()),
        z1=float(z.max()),
        theta_min=theta_min,
        theta_max=theta_max,
        full360=full360,
        max_rel_resid=max_rel,
    )


def cylinder_fit_to_faces(cf: "CylinderFit"):
    """Build analytic ``ada.geom`` cylindrical AdvancedFace(s) for a fitted member — the
    exact structure the STEP writer emits (CYLINDRICAL_SURFACE) and both CAD backends
    build. A full tube is split into ``ceil(sweep/π)`` arc segments (each ≤ 180°) so no
    face is periodic — sidestepping the seam-trim trap while staying analytic. Each face
    is one segment: top arc + seam line + bottom arc + seam line (mirrors the validated
    ``test_advanced_face_seam`` template). Returns list[AdvancedFace]."""
    import math

    from ada.geom.curves import Circle, EdgeCurve, EdgeLoop, Line, OrientedEdge
    from ada.geom.direction import Direction
    from ada.geom.placement import Axis2Placement3D
    from ada.geom.points import Point
    from ada.geom.surfaces import AdvancedFace, CylindricalSurface, FaceBound

    axis_d = Direction(*cf.axis)
    ref_d = Direction(*cf.e1)

    def _pt(theta: float, z: float) -> "Point":
        radial = math.cos(theta) * cf.e1 + math.sin(theta) * cf.e2
        return Point(*(cf.origin + cf.radius * radial + z * cf.axis))

    def _circle_at(z: float) -> Circle:
        loc = Point(*(cf.origin + z * cf.axis))
        return Circle(position=Axis2Placement3D(location=loc, axis=axis_d, ref_direction=ref_d), radius=cf.radius)

    def _arc(pa, pb, z) -> OrientedEdge:
        ec = EdgeCurve(start=pa, end=pb, edge_geometry=_circle_at(z), same_sense=True)
        return OrientedEdge(start=pa, end=pb, edge_element=ec, orientation=True)

    def _line(pa, pb) -> OrientedEdge:
        d = Direction(pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2])
        ec = EdgeCurve(start=pa, end=pb, edge_geometry=Line(pnt=pa, dir=d), same_sense=True)
        return OrientedEdge(start=pa, end=pb, edge_element=ec, orientation=True)

    t0 = 0.0 if cf.full360 else cf.theta_min
    t1 = t0 + (2.0 * math.pi if cf.full360 else (cf.theta_max - cf.theta_min))
    n_seg = max(1, math.ceil((t1 - t0) / (math.pi - 1e-6)))
    dth = (t1 - t0) / n_seg
    surf = CylindricalSurface(
        position=Axis2Placement3D(location=Point(*cf.origin), axis=axis_d, ref_direction=ref_d), radius=cf.radius
    )
    faces = []
    for k in range(n_seg):
        ta, tb = t0 + k * dth, t0 + (k + 1) * dth
        a_t, b_t = _pt(ta, cf.z1), _pt(tb, cf.z1)
        a_b, b_b = _pt(ta, cf.z0), _pt(tb, cf.z0)
        loop = EdgeLoop(edge_list=[_arc(a_t, b_t, cf.z1), _line(b_t, b_b), _arc(b_b, a_b, cf.z0), _line(a_b, a_t)])
        faces.append(AdvancedFace(bounds=[FaceBound(bound=loop, orientation=True)], face_surface=surf, same_sense=True))
    return faces


def _facet_flat_face(prims: "_Primitives", j: int):
    """One shell facet → a flat planar AdvancedFace (PolyLoop bound) — the analytic-emit
    fallback for patches that aren't recognised as a cylinder yet (small trim / freeform)."""
    from ada.geom.curves import PolyLoop
    from ada.geom.direction import Direction
    from ada.geom.placement import Axis2Placement3D
    from ada.geom.points import Point
    from ada.geom.surfaces import AdvancedFace, FaceBound, Plane

    pts = prims.outline(j)
    poly = [Point(*p) for p in pts]
    plane = Plane(position=Axis2Placement3D(location=Point(*pts[0]), axis=Direction(*prims.normals[j])))
    return AdvancedFace(
        bounds=[FaceBound(bound=PolyLoop(polygon=poly), orientation=True)], face_surface=plane, same_sense=True
    )


def _plane_bucket_components(prims: "_Primitives", idxs: list[int], ndigits: int) -> Iterator[list[int]]:
    """Edge-connected components of a primitive subset, split first by plane bucket —
    each component is a set of coplanar, edge-adjacent facets (one candidate flat face)."""
    tol = 10.0 ** (-ndigits)
    normals = prims.normals
    sign = _canonical_sign(normals, tol)
    ncanon = np.round(normals * sign[:, None], ndigits)
    thick_q = np.round(np.array(prims.ts), ndigits)
    buckets: dict = {}
    for j in idxs:
        p0 = prims.coords[prims.rows[j][0]]
        off = round(float(sign[j] * float(np.dot(normals[j], p0))), ndigits)
        buckets.setdefault((prims.mats[j], float(thick_q[j]), tuple(ncanon[j]), off), []).append(j)
    for bucket in buckets.values():
        if len(bucket) == 1:
            yield bucket
            continue
        parent = list(range(len(bucket)))

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        edge_owner: dict = {}
        for li, j in enumerate(bucket):
            r = prims.rows[j]
            k = len(r)
            for e in range(k):
                a, b = r[e], r[(e + 1) % k]
                ek = (a, b) if a <= b else (b, a)
                if ek in edge_owner:
                    ra, rb = find(li), find(edge_owner[ek])
                    if ra != rb:
                        parent[ra] = rb
                else:
                    edge_owner[ek] = li
        comps: dict = {}
        for li in range(len(bucket)):
            comps.setdefault(find(li), []).append(bucket[li])
        yield from comps.values()


def _flat_faces_with_holes(prims: "_Primitives", comp: list[int], ndigits: int) -> list:
    """A coplanar component → flat AdvancedFaces via robust boundary extraction: one face
    per material region (a pinch splits into several), each with FACE_OUTER_BOUND + any
    FACE_BOUND hole loops. Returns [] if the boundary won't resolve — caller falls back."""
    from ada.core.vector_utils import extract_boundary_loops
    from ada.geom.curves import PolyLoop
    from ada.geom.direction import Direction
    from ada.geom.placement import Axis2Placement3D
    from ada.geom.points import Point
    from ada.geom.surfaces import AdvancedFace, FaceBound, Plane

    res = extract_boundary_loops([prims.outline(j) for j in comp], ndigits=ndigits)
    if not res:
        return []
    n = prims.normals[comp[0]]
    faces = []
    for outer, holes in res:
        plane = Plane(position=Axis2Placement3D(location=Point(*outer[0]), axis=Direction(*n)))
        bounds = [FaceBound(bound=PolyLoop(polygon=[Point(*p) for p in outer]), orientation=True)]
        for h in holes:  # inner void loops (kept CW by the extractor)
            bounds.append(FaceBound(bound=PolyLoop(polygon=[Point(*p) for p in h]), orientation=True))
        faces.append(AdvancedFace(bounds=bounds, face_surface=plane, same_sense=True))
    return faces


def _analytic_flat_faces(prims: "_Primitives", patch: list[int], ndigits: int):
    """Non-cylinder patch → flat AdvancedFaces: merged faces (with holes, pinches split)
    per coplanar edge-connected component; single-loop merge or per-facet only where
    robust extraction can't resolve the boundary. Never worse than the coplanar merge."""
    for comp in _plane_bucket_components(prims, patch, ndigits):
        if len(comp) == 1:
            yield _facet_flat_face(prims, comp[0])
            continue
        faces = _flat_faces_with_holes(prims, comp, ndigits)
        if faces:
            yield from faces
            continue
        fd = _flat_face(prims, comp, ndigits)  # single clean loop (no holes)
        if fd is not None:
            yield _facedata_to_advanced_face(fd)
            continue
        for j in comp:  # give up: keep the facets
            yield _facet_flat_face(prims, j)


def _facedata_to_advanced_face(fd: "FaceData"):
    """A merged flat ``FaceData`` (outline polygon) → a flat planar AdvancedFace."""
    from ada.geom.curves import PolyLoop
    from ada.geom.direction import Direction
    from ada.geom.placement import Axis2Placement3D
    from ada.geom.points import Point
    from ada.geom.surfaces import AdvancedFace, FaceBound, Plane

    outline = np.asarray(fd.outline, dtype=float)
    poly = [Point(*p) for p in outline]
    plane = Plane(position=Axis2Placement3D(location=Point(*outline[0]), axis=Direction(*fd.normal)))
    return AdvancedFace(
        bounds=[FaceBound(bound=PolyLoop(polygon=poly), orientation=True)], face_surface=plane, same_sense=True
    )


def iter_fem_analytic_faces(
    part, *, angle_tol: float = 30.0, min_patch_quads: int = 12, ndigits: int = 6, trim_cylinders: bool = False
):
    """Yield analytic ``ada.geom`` faces for every FEM shell mesh under ``part``, auto-
    detecting each region-grown patch's primitive: a **cylinder** patch → analytic
    CYLINDRICAL_SURFACE face(s); anything else → the coplanar-merged flat faces of that
    patch (one merged plate per clean coplanar component, per-facet only where the merge
    can't collapse). No human guidance — ``classify_patch`` decides.

    ``trim_cylinders`` (default False) trims each tube to its real joint-cut boundary
    (exact ends, more faithful STEP). It is OFF by default because a joint cut's diagonal
    boundary edges are chords, not on-surface curves — OCC reads them, but the adacpp
    backend's stricter wire build can't mesh them curved (needs pcurves), so the default
    full-tube form (flat circular ends, exact tube *bodies*) stays viz-safe on BOTH CAD
    backends. Enable it for STEP-precision output that won't be tessellated via adacpp.

    Never worse than the plain coplanar merge (non-cylinder patches fall through to it)
    and collapses a tube's thousands of shell facets to a handful of exact cylinders."""
    parts = part.get_all_parts_in_assembly(include_self=True) if hasattr(part, "get_all_parts_in_assembly") else [part]
    for p in parts:
        fem = getattr(p, "fem", None)
        if fem is None or len(fem.elements) == 0:
            continue
        for blk in _shell_blocks(fem):
            prims = _block_primitives(blk)
            if len(prims) == 0:
                continue
            for patch in _surface_patches(prims, angle_tol, ndigits):
                if len(patch) >= min_patch_quads and classify_patch(prims, patch) == "cylinder":
                    cf = fit_cylinder_params(prims, patch)
                    if cf is not None:
                        # exact joint-cut trim only when asked (breaks adacpp meshing, see above);
                        # otherwise the viz-safe full tube with flat circular ends.
                        trimmed = cylinder_trim_faces(prims, patch, cf, ndigits) if trim_cylinders else None
                        yield from (trimmed if trimmed is not None else cylinder_fit_to_faces(cf))
                        continue
                # non-cylinder patch: coplanar-merge it into flat faces WITH holes
                # (robust boundary extraction), facet only where the boundary won't
                # resolve — never worse than the plain coplanar merge.
                if len(patch) == 1:
                    yield _facet_flat_face(prims, patch[0])
                else:
                    yield from _analytic_flat_faces(prims, patch, ndigits)


def cylinder_trim_faces(prims: "_Primitives", patch: list[int], cf: "CylinderFit", ndigits: int = 6):
    """Trim the fitted cylinder by the patch's ACTUAL boundary (the joint-cut curves)
    instead of flat circles at the axial extremes: one CYLINDRICAL_SURFACE face bounded by
    the patch's boundary loops, each edge an arc (consecutive nodes at ~equal axial height)
    or a chord line (otherwise) — all (near-)on the cylinder, which OCC accepts. Returns a
    list of AdvancedFace, or None if the boundary won't resolve (caller uses the full tube)."""
    from ada.core.vector_utils import boundary_cycles_3d
    from ada.geom.curves import Circle, EdgeCurve, EdgeLoop, Line, OrientedEdge
    from ada.geom.direction import Direction
    from ada.geom.placement import Axis2Placement3D
    from ada.geom.points import Point
    from ada.geom.surfaces import AdvancedFace, CylindricalSurface, FaceBound

    cycles = boundary_cycles_3d([prims.outline(j) for j in patch], ndigits=ndigits)
    if not cycles:
        return None
    axis_d = Direction(*cf.axis)
    ref_d = Direction(*cf.e1)
    z_tol = 1e-6 * max(cf.z1 - cf.z0, cf.radius, 1.0)

    def _axial(p):
        return float((np.asarray(p, dtype=float) - cf.origin) @ cf.axis)

    def _edge(pa, pb):
        Pa, Pb = Point(*pa), Point(*pb)
        if abs(_axial(pa) - _axial(pb)) <= z_tol:  # constant-height → circular arc on the cylinder
            loc = Point(*(cf.origin + _axial(pa) * cf.axis))
            g = Circle(position=Axis2Placement3D(location=loc, axis=axis_d, ref_direction=ref_d), radius=cf.radius)
        else:  # otherwise a chord (OCC tolerates the sub-mesh-step off-surface deviation)
            g = Line(pnt=Pa, dir=Direction(pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]))
        return OrientedEdge(
            start=Pa,
            end=Pb,
            edge_element=EdgeCurve(start=Pa, end=Pb, edge_geometry=g, same_sense=True),
            orientation=True,
        )

    bounds = []
    for cyc in cycles:
        if len(cyc) < 3:
            return None
        edges = [_edge(cyc[i], cyc[(i + 1) % len(cyc)]) for i in range(len(cyc))]
        bounds.append(FaceBound(bound=EdgeLoop(edge_list=edges), orientation=True))
    surf = CylindricalSurface(
        position=Axis2Placement3D(location=Point(*cf.origin), axis=axis_d, ref_direction=ref_d), radius=cf.radius
    )
    return [AdvancedFace(bounds=bounds, face_surface=surf, same_sense=True)]


def classify_patch(prims: "_Primitives", patch: list[int], *, plane_tol: float = 1e-3, cyl_tol: float = 0.02) -> str:
    """Classify a region-grown patch by the analytic surface it fits: ``planar`` (flat
    within ``plane_tol`` of the bbox diagonal), ``cylinder`` (radial normals, circle
    cross-section within ``cyl_tol`` of the radius, and an actual axial span), else
    ``freeform`` (→ B-spline). Planar is tested first (a flat patch is a degenerate
    cylinder)."""
    pts = np.vstack([prims.outline(j) for j in patch])
    _, plane_dev = _fit_plane(pts)
    if plane_dev <= plane_tol:
        return "planar"
    normals = np.array([prims.normals[j] for j in patch], dtype=float)
    _r, rel, span_over_r = _fit_cylinder(pts, normals)
    if rel <= cyl_tol and span_over_r > 0.3:
        return "cylinder"
    return "freeform"


def faces_from_fem(
    fem, strategy=MergeStrategy.COPLANAR, ndigits: int = 6, *, max_dev: float | None = None
) -> Iterator[FaceData]:
    """Yield merged CAD faces for a single ``FEM`` mesh (one part).

    The per-FEM core; :func:`iter_faces` walks an assembly and calls this for
    each part. ``Part.iter_objects_from_fem`` also delegates here so the object
    and object-free streams share one strategy-aware source.

    ``PLANAR`` grows flat patches (within ``max_dev``, auto if None) and emits one
    flat face per patch — the near-term FEM→CAD plate-count reducer that recovers
    large flat panels coplanar's exact bucketing misses and piecewise-flattens curved
    skin. ``SURFACE`` (curved → one B-spline face) is not wired into the writer yet."""
    strategy = MergeStrategy.from_value(strategy)
    if strategy in (MergeStrategy.SURFACE, MergeStrategy.PANEL):
        raise NotImplementedError(f"merge strategy {strategy.value!r} not yet wired into the vectorized face source")
    if fem is None or len(fem.elements) == 0:
        return
    for blk in _shell_blocks(fem):
        prims = _block_primitives(blk)
        if len(prims) == 0:
            continue
        if strategy == MergeStrategy.NONE:
            for j in range(len(prims)):
                yield prims.face(j)
        elif strategy == MergeStrategy.PLANAR:
            md = _auto_max_dev(prims) if max_dev is None else max_dev
            for patch in _planar_patches(prims, md, ndigits):
                if len(patch) == 1:
                    yield prims.face(patch[0])
                    continue
                face = _flat_face(prims, patch, ndigits)  # whole flat patch → one face
                if face is not None:
                    yield face
                else:  # boundary didn't collapse (hole/T-junction) → coplanar (never worse)
                    yield from _coplanar_subset(prims, patch, ndigits)
        else:
            yield from _coplanar_block(prims, ndigits)


def iter_faces(
    part, strategy=MergeStrategy.COPLANAR, ndigits: int = 6, *, max_dev: float | None = None
) -> Iterator[FaceData]:
    """Yield merged CAD faces for every FEM mesh under ``part`` (Part or Assembly).

    Object-free: walks each sub-part's array-backed shell mesh, never building
    Plate/Elem objects.
    """
    parts = part.get_all_parts_in_assembly(include_self=True) if hasattr(part, "get_all_parts_in_assembly") else [part]
    for p in parts:
        yield from faces_from_fem(getattr(p, "fem", None), strategy, ndigits, max_dev=max_dev)


def _coplanar_block(prims: _Primitives, ndigits: int) -> Iterator[FaceData]:
    tol = 10.0 ** (-ndigits)
    normals = prims.normals
    sign = _canonical_sign(normals, tol)
    ncanon = np.round(normals * sign[:, None], ndigits)
    p0 = prims.coords[np.array([r[0] for r in prims.rows], dtype=np.int64)]
    # offset from the RAW normal then signed (matches _plate_plane_key: it dots
    # the un-rounded normal with p0, applies the canonical sign, then rounds).
    offset = np.round(sign * np.sum(normals * p0, axis=1), ndigits)
    thick_q = np.round(np.array(prims.ts), ndigits)

    # plane bucket key: (material, thickness, canonical-normal, offset)
    buckets: dict[tuple, list[int]] = {}
    for j in range(len(prims)):
        key = (prims.mats[j], float(thick_q[j]), tuple(ncanon[j]), float(offset[j]))
        buckets.setdefault(key, []).append(j)

    for idxs in buckets.values():
        yield from _merge_plane_bucket(prims, idxs, ndigits)


def _merge_plane_bucket(prims: _Primitives, idxs: list[int], ndigits: int) -> Iterator[FaceData]:
    """Split a plane bucket into edge-connected components, merge each."""
    if len(idxs) == 1:
        yield prims.face(idxs[0])
        return

    parent = list(range(len(idxs)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # edge-connectivity by shared node identity (conformal mesh: shared edge ==
    # shared node rows); two primitives sharing an edge are in one component.
    edge_owner: dict[tuple, int] = {}
    for li, j in enumerate(idxs):
        r = prims.rows[j]
        k = len(r)
        for e in range(k):
            a, b = r[e], r[(e + 1) % k]
            ek = (a, b) if a <= b else (b, a)
            if ek in edge_owner:
                union(li, edge_owner[ek])
            else:
                edge_owner[ek] = li

    comps: dict[int, list[int]] = {}
    for li in range(len(idxs)):
        comps.setdefault(find(li), []).append(li)

    for comp in comps.values():
        cj = [idxs[li] for li in comp]
        if len(cj) == 1:
            yield prims.face(cj[0])
        else:
            yield from _merge_component(prims, cj, ndigits)


def _merge_component(prims: _Primitives, cj: list[int], ndigits: int) -> Iterator[FaceData]:
    from ada.core.vector_utils import (
        merge_coplanar_loops_by_edge_cancellation,
        project_points_to_local_2d,
    )
    from ada.fem.formats.concept_merge import _loop_is_simple_2d

    ref = cj[0]
    name = f"{prims.names[ref]}_m"
    loops = [prims.outline(j) for j in cj]
    merged = merge_coplanar_loops_by_edge_cancellation(loops, ndigits=ndigits)
    if merged is not None:
        try:
            pts2d, _ = project_points_to_local_2d(merged)
            if _loop_is_simple_2d(pts2d):
                outline = np.asarray(merged, dtype=float)
                yield FaceData(name, outline, _newell_normals(outline[None])[0], prims.mats[ref], prims.ts[ref])
                return
        except Exception as exc:  # best-effort: degenerate merge -> keep originals
            logger.debug(f"coplanar merge fell back for {name!r}: {exc}")
    # best-effort contract: merge only when it collapses cleanly, else keep all.
    for j in cj:
        yield prims.face(j)


def _flat_face(prims: _Primitives, cj: list[int], ndigits: int) -> "FaceData | None":
    """The success path of :func:`_merge_component`: merge a patch's boundary to one
    clean simple loop → one flat ``FaceData``, or None if it doesn't collapse cleanly
    (hole / T-junction / non-manifold → the all-or-nothing boundary limit)."""
    from ada.core.vector_utils import (
        merge_coplanar_loops_by_edge_cancellation,
        project_points_to_local_2d,
    )
    from ada.fem.formats.concept_merge import _loop_is_simple_2d

    merged = merge_coplanar_loops_by_edge_cancellation([prims.outline(j) for j in cj], ndigits=ndigits)
    if merged is None:
        return None
    try:
        pts2d, _ = project_points_to_local_2d(merged)
    except Exception:  # noqa: BLE001
        return None
    if not _loop_is_simple_2d(pts2d):
        return None
    outline = np.asarray(merged, dtype=float)
    ref = cj[0]
    return FaceData(f"{prims.names[ref]}_m", outline, _newell_normals(outline[None])[0], prims.mats[ref], prims.ts[ref])


def _coplanar_subset(prims: _Primitives, idxs: list[int], ndigits: int) -> Iterator[FaceData]:
    """Coplanar-merge a subset of primitives (plane buckets → edge-connected components
    → merge each). The planar strategy's fallback when a grown patch's boundary won't
    collapse: it can never emit more faces than the plain coplanar merge would."""
    tol = 10.0 ** (-ndigits)
    normals = prims.normals
    sign = _canonical_sign(normals, tol)
    ncanon = np.round(normals * sign[:, None], ndigits)
    thick_q = np.round(np.array(prims.ts), ndigits)
    buckets: dict = {}
    for j in idxs:
        p0 = prims.coords[prims.rows[j][0]]
        off = round(float(sign[j] * float(np.dot(normals[j], p0))), ndigits)
        key = (prims.mats[j], float(thick_q[j]), tuple(ncanon[j]), off)
        buckets.setdefault(key, []).append(j)
    for b in buckets.values():
        yield from _merge_plane_bucket(prims, b, ndigits)
