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


def _combined_shell_primitives(fem) -> "_Primitives | None":
    """All shell elements of ``fem`` as ONE primitive set (quads + triangles together) so patch
    growing merges across element types. ``_shell_blocks`` splits by element type; a triangle among
    quads (transition/filler) would then have no same-block neighbour and strand as a single-facet
    plate. Every block shares the store's coord array, so their node-row indices are directly
    concatenable and shared-edge adjacency works across tri/quad."""
    coords = None
    rows: list = []
    names: list = []
    mats: list = []
    ts: list = []
    norms: list = []
    for blk in _shell_blocks(fem):
        prims = _block_primitives(blk)
        if len(prims) == 0:
            continue
        coords = prims.coords
        rows.extend(prims.rows)
        names.extend(prims.names)
        mats.extend(prims.mats)
        ts.extend(prims.ts)
        norms.append(prims.normals)
    if coords is None:
        return None
    return _Primitives(coords, rows, names, mats, ts, np.vstack(norms))


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


def _plane_bucket_components(
    prims: "_Primitives", idxs: list[int], ndigits: int, plane_digits: int | None = None
) -> Iterator[list[int]]:
    """Edge-connected components of a primitive subset, split first by plane bucket —
    each component is a set of coplanar, edge-adjacent facets (one candidate flat face).

    Bucket key = (material, thickness, canonical NORMAL direction). NO offset-along-normal: two
    edge-connected facets with the same normal necessarily share a plane (they share an edge, so
    the same-normal plane through it is the same), so connectivity already separates distinct
    parallel planes — while an offset key would stair-step a slightly-curved surface (a slanted
    roof) into many parallel plates with gaps between the steps. ``plane_digits`` (default
    ``ndigits``) rounds the normal — coarser than the exact coordinate precision (e.g. 3) so
    mesh-noisy coplanar facets share a bucket, and so a piecewise-planar roof splits by its slopes
    (distinct normals) rather than merging into one averaged plane."""
    pd = ndigits if plane_digits is None else plane_digits
    tol = 10.0 ** (-pd)
    normals = prims.normals
    sign = _canonical_sign(normals, tol)
    ncanon = np.round(normals * sign[:, None], pd)
    thick_q = np.round(np.array(prims.ts), ndigits)
    buckets: dict = {}
    for j in idxs:
        buckets.setdefault((prims.mats[j], float(thick_q[j]), tuple(ncanon[j])), []).append(j)
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
    from ada.core.vector_utils import extract_boundary_loops, simplify_closed_polygon
    from ada.geom.curves import PolyLoop
    from ada.geom.direction import Direction
    from ada.geom.placement import Axis2Placement3D
    from ada.geom.points import Point
    from ada.geom.surfaces import AdvancedFace, FaceBound, Plane

    res = extract_boundary_loops([prims.outline(j) for j in comp], ndigits=ndigits)
    if not res:
        return []
    # Best-fit plane of the WHOLE patch (not the first facet's normal): a gently-curved patch (a
    # slanted roof, plane_dev ~1%) then becomes ONE flat plate centred through it, instead of being
    # stair-stepped into many offset buckets with gaps between the steps.
    n, _dev = _fit_plane(np.vstack([prims.outline(j) for j in comp]))
    faces = []
    for outer, holes in res:
        # The union-boundary tracer follows every facet edge, so a merged plate's boundary
        # zigzags (a rectangle carries dozens of near-collinear points → libtess2 triangulates
        # them into "etched" noise). Douglas-Peucker collapses each near-collinear run to its
        # corners. A TIGHT tolerance: at 3% it dropped a real vertex like the shallow apex of a
        # gable-end triangle (the ridge peak), cutting the plate's top off so it no longer met the
        # roof — a gap. 0.4% only removes genuinely-collinear mesh nodes and keeps every real corner,
        # so a shared (slanted/peaked) edge stays coincident with the neighbouring plate's.
        outer = simplify_closed_polygon(outer, rel_tol=0.004, max_area_change=0.01)
        holes = [simplify_closed_polygon(h, rel_tol=0.004, max_area_change=0.01) for h in holes]
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


def _elid_of(name: str) -> int:
    """Element id from a primitive name ('sh123' or 'sh123_1' → 123)."""
    return int(name[2:].split("_")[0])


def _quad_rings_by_elid(blk: "_ShellBlock") -> dict:
    """el_id → its 4 corner coords, for the block's QUAD elements (grid-fit candidates).
    Empty for triangle blocks (no structured grid to recover)."""
    if blk.conn.shape[1] != 4:
        return {}
    return {
        int(blk.el_ids[i]): [tuple(float(x) for x in blk.coords[n]) for n in blk.conn[i][:4]]
        for i in range(blk.conn.shape[0])
    }


def _grow_smooth_region(seed, keys, normals, edge_owner, cos_tol, visited):
    """BFS a smooth quad region from ``seed``, assigning each quad integer (gi, gj) grid
    coords, CROSSING T-junctions: at an edge with >2 owners (a stiffener attaching to the
    hull) the single *smooth*-continuous neighbour is followed and the folded face(s) ignored,
    so a hull panel grows straight across stiffener lines. Coord is first-wins (a later
    conflicting placement is dropped, not poisoned) — the caller tiles the result into
    conflict-free rectangles. Returns (comp quad indices, {node_key: (gi, gj)})."""
    from collections import deque

    _cell = ((0, 0), (1, 0), (1, 1), (0, 1))
    coord: dict = {}
    for k, off in zip(keys[seed], _cell):
        coord[k] = off
    comp = [seed]
    visited[seed] = True
    q = deque([seed])
    while q:
        qi = q.popleft()
        ring = keys[qi]
        for e in range(4):
            a, b = ring[e], ring[(e + 1) % 4]
            owners = edge_owner.get((a, b) if a <= b else (b, a), ())
            cands = [o for o in owners if o != qi and abs(float(np.dot(normals[qi], normals[o]))) >= cos_tol]
            if len(cands) != 1:  # boundary / all-fold / ambiguous → stop growth across this edge
                continue
            nb = cands[0]
            prev_a = ring[(e - 1) % 4]
            qx, qy = coord[prev_a][0] - coord[a][0], coord[prev_a][1] - coord[a][1]
            new_a = (coord[a][0] - qx, coord[a][1] - qy)
            new_b = (coord[b][0] - qx, coord[b][1] - qy)
            nbring = keys[nb]
            try:
                ia, ib = nbring.index(a), nbring.index(b)
            except ValueError:
                continue
            if ib == (ia + 1) % 4:
                ka, kb = nbring[(ia - 1) % 4], nbring[(ib + 1) % 4]
            elif ib == (ia - 1) % 4:
                ka, kb = nbring[(ia + 1) % 4], nbring[(ib - 1) % 4]
            else:
                continue
            for k, c in ((ka, new_a), (kb, new_b)):
                coord.setdefault(k, c)  # first-wins; conflicts resolved by the tiler
            if not visited[nb]:
                visited[nb] = True
                comp.append(nb)
                q.append(nb)
    return comp, coord


def _max_filled_rectangle(cells: set):
    """Largest all-filled axis-aligned rectangle of grid cells (``{(gi, gj)}``) → inclusive
    ``(i0, j0, i1, j1)`` or None. Histogram/stack maximal-rectangle over the bounding box."""
    if not cells:
        return None
    i0 = min(c[0] for c in cells)
    i1 = max(c[0] for c in cells)
    j0 = min(c[1] for c in cells)
    j1 = max(c[1] for c in cells)
    w = j1 - j0 + 1
    heights = [0] * w
    best_area = 0
    best = None
    for i in range(i0, i1 + 1):
        for c in range(w):
            heights[c] = heights[c] + 1 if (i, j0 + c) in cells else 0
        stack: list = []  # (start_col, height)
        for c in range(w + 1):
            h = heights[c] if c < w else 0
            start = c
            while stack and stack[-1][1] > h:
                st, sh = stack.pop()
                area = sh * (c - st)
                if area > best_area:
                    best_area = area
                    best = (i - sh + 1, j0 + st, i, j0 + c - 1)
                start = st
            stack.append((start, h))
    return best


def _reconstruct_curved_panels(blk, exclude_elids, ndigits, angle_tol, min_patch_quads, plane_tol=1e-3):
    """Curved-panel B-spline pass over a block's QUAD elements. Grows smooth regions across
    stiffener T-junctions, tiles each into maximal conflict-free rectangles, and emits ONE
    degree-1 B-spline surface face per rectangle that is genuinely CURVED (a planar rectangle
    is left to the flat merge, which represents it better). Cylinder-patch elements are
    excluded so analytic CYLINDRICAL_SURFACE emit is preserved. Returns one ``(AdvancedFace,
    [el_id, ...])`` per curved panel — the emit yields the faces, the preview colours the
    source elements by panel. OCC-free: grower + native surface builder run on every backend."""
    from ada.fem.formats.concept_merge import _round_key
    from ada.fem.formats.surface_reconstruction import _quad_normal

    qr = _quad_rings_by_elid(blk)
    elids = [e for e in qr if e not in exclude_elids]
    if len(elids) < min_patch_quads:
        return []
    rings = [qr[e] for e in elids]
    keys = [tuple(_round_key(p, ndigits) for p in r) for r in rings]
    normals = [_quad_normal(r) for r in rings]
    edge_owner: dict = {}
    for qi, ring in enumerate(keys):
        for a, b in zip(ring, ring[1:] + ring[:1]):
            edge_owner.setdefault((a, b) if a <= b else (b, a), []).append(qi)
    cos_tol = float(np.cos(np.deg2rad(angle_tol)))
    visited = [False] * len(rings)
    panels: list = []  # (AdvancedFace, [el_id, ...]) — one per curved rectangle
    for seed in range(len(rings)):
        if visited[seed]:
            continue
        comp, coord = _grow_smooth_region(seed, keys, normals, edge_owner, cos_tol, visited)
        # cell → quad + node positions (drop quads whose 4 corners aren't a clean unit cell)
        cell_quad: dict = {}
        node_pos: dict = {}
        for qi in comp:
            cs = [coord.get(k) for k in keys[qi]]
            if any(c is None for c in cs):
                continue
            gi0 = min(c[0] for c in cs)
            gj0 = min(c[1] for c in cs)
            if sorted((c[0] - gi0, c[1] - gj0) for c in cs) != [(0, 0), (0, 1), (1, 0), (1, 1)]:
                continue
            if (gi0, gj0) in cell_quad:
                continue  # two quads claim one cell → conflict, skip both cells' owner
            cell_quad[(gi0, gj0)] = qi
            for k, p in zip(keys[qi], rings[qi]):
                node_pos[coord[k]] = p
        cells = set(cell_quad)
        while cells:  # greedily tile into maximal rectangles
            rect = _max_filled_rectangle(cells)
            if rect is None:
                break
            r0, c0, r1, c1 = rect
            if (r1 - r0 + 1) * (c1 - c0 + 1) < min_patch_quads:
                break
            grid = []
            good = True
            for gi in range(r0, r1 + 2):
                row = []
                for gj in range(c0, c1 + 2):
                    p = node_pos.get((gi, gj))
                    if p is None:
                        good = False
                        break
                    row.append(p)
                if not good:
                    break
                grid.append(row)
            rquads = [cell_quad[(gi, gj)] for gi in range(r0, r1 + 1) for gj in range(c0, c1 + 1)]
            for gi in range(r0, r1 + 1):  # consume these cells regardless of outcome
                for gj in range(c0, c1 + 1):
                    cells.discard((gi, gj))
            if not good:
                continue
            pts = np.array([p for row in grid for p in row], dtype=float)
            if _fit_plane(pts)[1] <= plane_tol:
                continue  # flat rectangle → leave for the (better) planar merge
            af = _fit_cubic_bspline_surface_face_from_grid(grid)
            if af is not None:
                panels.append((af, [elids[qi] for qi in rquads]))
    return panels


def _bspline_surface_face_from_grid(grid):
    """A structured nu×nv node grid → ONE degree-1 tensor-product B-spline AdvancedFace,
    built directly (no OCC, no fit). Control points = the grid nodes with clamped knots, so
    the surface passes through EVERY node and is bilinear between them — i.e. geometrically
    identical to the mesh facets, with zero deviation — but the whole curved patch is a single
    face. Boundary = the grid perimeter (which lies exactly on a degree-1 surface). None if
    the grid is degenerate."""
    from ada.geom.curves import KnotType, PolyLoop
    from ada.geom.points import Point
    from ada.geom.surfaces import AdvancedFace, BSplineSurfaceForm, BSplineSurfaceWithKnots, FaceBound

    nu = len(grid)
    nv = len(grid[0]) if nu else 0
    if nu < 2 or nv < 2:
        return None

    def _clamped(n):  # degree-1 clamped knot vector: 0..n-1, ends doubled
        return [float(k) for k in range(n)], [2] + [1] * (n - 2) + [2]

    uk, um = _clamped(nu)
    vk, vm = _clamped(nv)
    surf = BSplineSurfaceWithKnots(
        u_degree=1,
        v_degree=1,
        control_points_list=[[Point(*grid[i][j]) for j in range(nv)] for i in range(nu)],
        surface_form=BSplineSurfaceForm.UNSPECIFIED,
        u_closed=False,
        v_closed=False,
        self_intersect=False,
        u_multiplicities=um,
        v_multiplicities=vm,
        u_knots=uk,
        v_knots=vk,
        knot_spec=KnotType.UNSPECIFIED,
    )
    # perimeter of the grid (CCW), on the surface exactly for degree 1
    perim = (
        [grid[0][j] for j in range(nv)]
        + [grid[i][nv - 1] for i in range(1, nu)]
        + [grid[nu - 1][j] for j in range(nv - 2, -1, -1)]
        + [grid[i][0] for i in range(nu - 2, 0, -1)]
    )
    bound = FaceBound(bound=PolyLoop(polygon=[Point(*p) for p in perim]), orientation=True)
    return AdvancedFace(bounds=[bound], face_surface=surf, same_sense=True)


def _clamped_uniform_knots(ncp: int, deg: int):
    """Clamped uniform B-spline knots for ``ncp`` control points of degree ``deg``.
    Returns (distinct_knots, multiplicities, flat_knot_vector)."""
    n_int = ncp - deg - 1  # interior knots
    interior = [(k + 1) / (n_int + 1) for k in range(n_int)] if n_int > 0 else []
    distinct = [0.0] + interior + [1.0]
    mults = [deg + 1] + [1] * len(interior) + [deg + 1]
    flat = [0.0] * (deg + 1) + interior + [1.0] * (deg + 1)
    return distinct, mults, flat


def _bspline_basis_matrix(params, flat_knots, deg: int, ncp: int):
    """Cox–de-Boor basis matrix: row r = the ``ncp`` basis-function values at ``params[r]``."""
    T = np.asarray(flat_knots, dtype=float)
    x = np.asarray(params, dtype=float)
    nspan = len(T) - 1
    M = np.zeros((len(x), ncp))
    for r, u in enumerate(x):
        N = np.zeros(nspan)
        if u >= T[-1]:  # right endpoint → last non-degenerate span
            for i in range(nspan - 1, -1, -1):
                if T[i] < T[i + 1]:
                    N[i] = 1.0
                    break
        else:
            for i in range(nspan):
                if T[i] <= u < T[i + 1]:
                    N[i] = 1.0
                    break
        for d in range(1, deg + 1):  # in-place front-to-back (reads N[i], N[i+1]; writes N[i])
            for i in range(nspan - d):
                a = (u - T[i]) / (T[i + d] - T[i]) * N[i] if T[i + d] > T[i] else 0.0
                b = (T[i + d + 1] - u) / (T[i + d + 1] - T[i + 1]) * N[i + 1] if T[i + d + 1] > T[i + 1] else 0.0
                N[i] = a + b
        M[r, :] = N[:ncp]
    return M


def _fit_cubic_bspline_surface_face_from_grid(grid, rel_tol: float = 0.01, max_cp: int = 16, deg: int = 3):
    """A structured nu×nv node grid → ONE **coarse bicubic** tensor-product B-spline AdvancedFace,
    least-squares fit with far fewer control points than nodes (a smooth hull panel → ~10×10
    control net instead of the full mesh). Adaptive: grows the control net until the max node
    deviation is within ``rel_tol`` × the patch size, else falls back to the exact degree-1
    surface (never worse than :func:`_bspline_surface_face_from_grid`)."""
    from ada.geom.curves import KnotType, PolyLoop
    from ada.geom.points import Point
    from ada.geom.surfaces import AdvancedFace, BSplineSurfaceForm, BSplineSurfaceWithKnots, FaceBound

    Q = np.asarray(grid, dtype=float)
    if Q.ndim != 3 or Q.shape[0] < deg + 1 or Q.shape[1] < deg + 1:
        return _bspline_surface_face_from_grid(grid)
    nu, nv = Q.shape[0], Q.shape[1]
    flat = Q.reshape(-1, 3)
    scale = float(np.linalg.norm(flat.max(0) - flat.min(0)))
    tol = rel_tol * scale
    u = np.linspace(0.0, 1.0, nu)
    v = np.linspace(0.0, 1.0, nv)

    best = None
    for cp in (6, 10, max_cp):
        m, n = min(cp, nu), min(cp, nv)
        if m < deg + 1 or n < deg + 1:
            continue
        ud, um, uf = _clamped_uniform_knots(m, deg)
        vd, vm, vf = _clamped_uniform_knots(n, deg)
        Nu = _bspline_basis_matrix(u, uf, deg, m)
        Nv = _bspline_basis_matrix(v, vf, deg, n)
        Nui, Nvi = np.linalg.pinv(Nu), np.linalg.pinv(Nv)
        P = np.stack([Nui @ Q[:, :, c] @ Nvi.T for c in range(3)], axis=-1)  # (m,n,3) control net
        Qf = np.stack([Nu @ P[:, :, c] @ Nv.T for c in range(3)], axis=-1)  # fitted surface @ grid
        dev = float(np.linalg.norm(Qf - Q, axis=-1).max())
        best = (P, Qf, ud, um, vd, vm, m, n)
        if dev <= tol:
            break
    else:
        if best is None or dev > tol:
            return _bspline_surface_face_from_grid(grid)  # can't fit coarsely → exact degree-1
    P, Qf, ud, um, vd, vm, m, n = best

    surf = BSplineSurfaceWithKnots(
        u_degree=deg,
        v_degree=deg,
        control_points_list=[[Point(*P[i][j]) for j in range(n)] for i in range(m)],
        surface_form=BSplineSurfaceForm.UNSPECIFIED,
        u_closed=False,
        v_closed=False,
        self_intersect=False,
        u_multiplicities=um,
        v_multiplicities=vm,
        u_knots=ud,
        v_knots=vd,
        knot_spec=KnotType.UNSPECIFIED,
    )

    # Boundary: the ACTUAL grid-edge node positions (Q), not the fitted-surface perimeter (Qf). The
    # fitted cubic's perimeter drifts off the real mesh edge and, coarsened, chords across it — so the
    # panel pulls in from its true border and leaves a gap against the adjacent (flat) face that
    # shares those nodes. Using Q keeps the edge on the shared mesh nodes; collapse only collinear
    # runs so a straight/slanted border becomes its endpoints (not a rectangle).
    from ada.core.vector_utils import simplify_closed_polygon

    top = [Q[0][j] for j in range(nv)]
    right = [Q[i][nv - 1] for i in range(1, nu)]
    bot = [Q[nu - 1][j] for j in range(nv - 2, -1, -1)]
    left = [Q[i][0] for i in range(nu - 2, 0, -1)]
    perim = [tuple(float(c) for c in p) for p in (top + right + bot + left)]
    perim = simplify_closed_polygon(perim, rel_tol=0.02, max_area_change=0.02)
    bound = FaceBound(bound=PolyLoop(polygon=[Point(*p) for p in perim]), orientation=True)
    return AdvancedFace(bounds=[bound], face_surface=surf, same_sense=True)


def iter_fem_analytic_faces(
    part,
    *,
    angle_tol: float = 30.0,
    min_patch_quads: int = 12,
    ndigits: int = 6,
    trim_cylinders: bool = True,
    reconstruct_curved: bool = True,
    skip_cylinders: bool = False,
    drop_on_tube=None,
):
    """Yield analytic ``ada.geom`` faces for every FEM shell mesh under ``part``, auto-
    detecting each region-grown patch's primitive: a **cylinder** patch → analytic
    CYLINDRICAL_SURFACE face(s); anything else → the coplanar-merged flat faces of that
    patch (one merged plate per clean coplanar component, per-facet only where the merge
    can't collapse). No human guidance — ``classify_patch`` decides.

    ``trim_cylinders`` (default True) trims each tube to its real joint-cut boundary (exact
    ends). Each trim edge carries a 2D pcurve on the cylinder, so the diagonal joint-cut
    edges tessellate curved on BOTH CAD backends (adacpp routes the pcurve through
    ``edge_from_pcurve``); set it False for the plain full-tube form (flat circular ends).

    ``reconstruct_curved`` (default True) is the curved-panel pass: over each block's quads it
    grows smooth regions ACROSS stiffener T-junctions, tiles them into maximal conflict-free
    rectangles, and emits ONE degree-1 B-spline surface face per CURVED rectangle (a
    5000-facet hull panel → one BSplineSurfaceWithKnots, exact & OCC-free). Cylinder regions
    are excluded (kept analytic) and flat rectangles are left to the planar merge.

    Never worse than the plain coplanar merge (non-reconstructed regions fall through to it)
    and collapses a tube's thousands of shell facets to a handful of exact cylinders."""
    parts = part.get_all_parts_in_assembly(include_self=True) if hasattr(part, "get_all_parts_in_assembly") else [part]
    for p in parts:
        fem = getattr(p, "fem", None)
        if fem is None or len(fem.elements) == 0:
            continue
        # Grow patches over ALL shell elements at once (quads + triangles together): a triangle
        # sitting among quads (transition/filler, common near openings and the top of a hull) is
        # edge-adjacent to them via shared node rows, so it joins their patch instead of becoming an
        # isolated single-facet plate. Splitting by element type stranded every such triangle.
        prims = _combined_shell_primitives(fem)
        if prims is None or len(prims) == 0:
            continue
        patches = list(_surface_patches(prims, angle_tol, ndigits))
        patch_cls = [(pt, classify_patch(prims, pt) if len(pt) >= min_patch_quads else "planar") for pt in patches]

        # Curved-panel B-spline pass over each QUAD block's structured grid (cylinder patches
        # excluded, kept analytic). Consumes only the curved rectangles; everything else falls to
        # the cylinder / planar / facet emit below, which skips the consumed elements.
        consumed: set = set()
        if reconstruct_curved:
            cyl_elids = {_elid_of(prims.names[j]) for pt, c in patch_cls if c == "cylinder" for j in pt}
            for blk in _shell_blocks(fem):
                if blk.conn.shape[1] != 4:
                    continue
                for face, panel_elids in _reconstruct_curved_panels(
                    blk, cyl_elids, ndigits, angle_tol, min_patch_quads
                ):
                    yield face
                    consumed.update(panel_elids)

        # Cylinders emit as analytic tubes (region-grow finds them by swept normals); their prims
        # are excluded from the flat merge below.
        cyl_prims: set = set()
        for patch, cls in patch_cls:
            if cls != "cylinder":
                continue
            cyl_prims.update(patch)
            if skip_cylinders:  # solids mode owns the tubes (emitted as CSG solids elsewhere)
                continue
            cf = fit_cylinder_params(prims, patch)
            if cf is not None:
                # exact joint-cut trim only when asked (breaks adacpp meshing, see above);
                # otherwise the viz-safe full tube with flat circular ends.
                trimmed = cylinder_trim_faces(prims, patch, cf, ndigits) if trim_cylinders else None
                yield from (trimmed if trimmed is not None else cylinder_fit_to_faces(cf))

        # Flat plates: group every remaining facet (not a cylinder, not consumed by a curved B-spline
        # panel) by plane bucket — (material, thickness, normal) + edge-connected components — and
        # merge each component into one flat face on its OWN plane. Grouping by normal keeps a
        # piecewise-planar roof as its separate slopes (each an exactly-flat plate that shares its
        # true node edges with the walls); dropping the offset key avoids stair-stepping a slope.
        flat = [
            j
            for j in range(len(prims))
            if j not in cyl_prims and (not consumed or _elid_of(prims.names[j]) not in consumed)
        ]
        if drop_on_tube is not None and flat:
            # solids mode: drop facets lying on a tube wall (they'd float on the CSG solid).
            flat = [j for j in flat if not drop_on_tube(prims.outline(j).mean(axis=0))]
        for comp in _plane_bucket_components(prims, flat, ndigits, plane_digits=3):
            if len(comp) == 1:
                yield _facet_flat_face(prims, comp[0])
                continue
            faces = _flat_faces_with_holes(prims, comp, ndigits)
            if faces:
                yield from faces
            else:  # boundary wouldn't resolve → per-facet (never lose geometry)
                for j in comp:
                    yield _facet_flat_face(prims, j)


@dataclass
class _Tube:
    """One tube member for the solids path: a fitted cylinder's mid-surface radius ``r`` + wall
    ``t`` over an axial band ``[z0, z1]`` (in the fit's own axis parameter). ``ro``/``ri`` are the
    outer/inner radii; ``p0``/``p1`` the axis endpoints."""

    origin: np.ndarray
    axis: np.ndarray
    e1: np.ndarray
    r: float
    t: float
    z0: float
    z1: float

    @property
    def ro(self) -> float:
        return self.r + self.t / 2.0

    @property
    def ri(self) -> float:
        return max(self.r - self.t / 2.0, 1e-6)

    @property
    def p0(self) -> np.ndarray:
        return self.origin + self.z0 * self.axis

    @property
    def p1(self) -> np.ndarray:
        return self.origin + self.z1 * self.axis


def _tube_placement(origin, axis, e1, z0: float):
    from ada.geom.placement import Axis2Placement3D, Direction

    start = np.asarray(origin, float) + z0 * np.asarray(axis, float)
    return Axis2Placement3D(location=tuple(start), axis=Direction(*axis), ref_direction=Direction(*e1))


def _filled_cylinder(origin, axis, e1, r: float, z0: float, z1: float):
    """A solid (filled) circular ExtrudedAreaSolid from ``z0`` to ``z1`` along ``axis`` — used as a
    boolean cutting tool and as the wall operands for a CUT member (a hollow profile-with-void
    extrusion has inner-wall winding Manifold rejects as a boolean minuend, so a cut member's wall
    is composed as outer_solid − inner_solid)."""
    from ada.geom.curves import Circle
    from ada.geom.placement import Axis2Placement3D, Direction
    from ada.geom.surfaces import ArbitraryProfileDef, ProfileType
    import ada.geom.solids as geo_so

    prof = ArbitraryProfileDef(ProfileType.AREA, Circle(Axis2Placement3D(), float(r)), [])
    return geo_so.ExtrudedAreaSolid(prof, _tube_placement(origin, axis, e1, z0), float(z1 - z0), Direction(0.0, 0.0, 1.0))


def _hollow_extrusion(origin, axis, e1, ro: float, ri: float, z0: float, z1: float):
    """A hollow tube as ONE ExtrudedAreaSolid whose profile is an outer circle with an inner-circle
    void (IfcArbitraryProfileDefWithVoids). This is the clean form for an UNCUT member — no boolean,
    so no Manifold artifacts (the outer_solid − inner_solid boolean introduces degenerate +
    non-manifold triangles from the long sliver wall triangles; the void profile is watertight)."""
    from ada.geom.curves import Circle
    from ada.geom.placement import Axis2Placement3D, Direction
    from ada.geom.surfaces import ArbitraryProfileDef, ProfileType
    import ada.geom.solids as geo_so

    prof = ArbitraryProfileDef(
        ProfileType.AREA,
        Circle(Axis2Placement3D(), float(ro)),
        [Circle(Axis2Placement3D(), float(ri))],
    )
    return geo_so.ExtrudedAreaSolid(prof, _tube_placement(origin, axis, e1, z0), float(z1 - z0), Direction(0.0, 0.0, 1.0))


def _seg_seg_distance(p1, q1, p2, q2) -> float:
    """Shortest distance between 3D segments [p1,q1] and [p2,q2] (Ericson, Real-Time Collision
    Detection). Used to decide whether two tube members actually intersect (form a joint)."""
    d1 = q1 - p1
    d2 = q2 - p2
    r = p1 - p2
    a = float(d1 @ d1)
    e = float(d2 @ d2)
    f = float(d2 @ r)
    if a < 1e-12 and e < 1e-12:
        return float(np.linalg.norm(r))
    if a < 1e-12:
        s, t = 0.0, float(np.clip(f / e, 0.0, 1.0))
    else:
        c = float(d1 @ r)
        if e < 1e-12:
            t, s = 0.0, float(np.clip(-c / a, 0.0, 1.0))
        else:
            b = float(d1 @ d2)
            den = a * e - b * b
            s = float(np.clip((b * f - c * e) / den, 0.0, 1.0)) if den > 1e-12 else 0.0
            t = (b * s + f) / e
            if t < 0.0:
                t, s = 0.0, float(np.clip(-c / a, 0.0, 1.0))
            elif t > 1.0:
                t, s = 1.0, float(np.clip((b - c) / a, 0.0, 1.0))
    return float(np.linalg.norm((p1 + d1 * s) - (p2 + d2 * t)))


def _collect_tubes(part, angle_tol: float, min_patch_quads: int, ndigits: int) -> "list[_Tube]":
    """Every cylinder patch across the assembly, one :class:`_Tube` per (patch, thickness band) —
    a patch spanning two shell thicknesses (e.g. a joint can) splits into stacked bands so each
    solid carries its own wall thickness (never merge across thicknesses)."""
    parts = part.get_all_parts_in_assembly(include_self=True) if hasattr(part, "get_all_parts_in_assembly") else [part]
    tubes: list[_Tube] = []
    for p in parts:
        fem = getattr(p, "fem", None)
        if fem is None or len(fem.elements) == 0:
            continue
        # combined tri+quad prims so tube detection matches iter_fem_analytic_faces (a tube meshed
        # with a few triangles among its quads is one patch, not a quad patch + stranded triangles).
        prims = _combined_shell_primitives(fem)
        if prims is None or len(prims) == 0:
            continue
        for patch in _surface_patches(prims, angle_tol, ndigits):
            if len(patch) < min_patch_quads or classify_patch(prims, patch) != "cylinder":
                continue
            cf = fit_cylinder_params(prims, patch)
            if cf is None:
                continue
            origin = np.asarray(cf.origin, float)
            axis = np.asarray(cf.axis, float)
            e1 = np.asarray(cf.e1, float)
            bands: dict[float, list[int]] = {}
            for j in patch:
                bands.setdefault(round(float(prims.ts[j]), ndigits), []).append(j)
            for t, js in bands.items():
                nodes = np.unique(np.concatenate([np.asarray(prims.rows[j]) for j in js]))
                axc = (prims.coords[nodes] - origin) @ axis
                z0, z1 = float(axc.min()), float(axc.max())
                if z1 - z0 < 1e-4:
                    continue
                tubes.append(_Tube(origin, axis, e1, float(cf.radius), float(t), z0, z1))
    return tubes


def _tube_neighbors(tubes: "list[_Tube]", parallel_cos: float = 0.96) -> "dict[int, list[int]]":
    """i -> tubes that form a JOINT with tube i: axes non-parallel (|cos| < ``parallel_cos``, so a
    collinear same-tube continuation is NOT treated as a joint) and axis segments closer than the
    sum of outer radii (they actually intersect)."""
    neigh: dict[int, list[int]] = {i: [] for i in range(len(tubes))}
    for i in range(len(tubes)):
        ti = tubes[i]
        for j in range(len(tubes)):
            if i == j:
                continue
            tj = tubes[j]
            if abs(float(ti.axis @ tj.axis)) >= parallel_cos:
                continue
            if _seg_seg_distance(ti.p0, ti.p1, tj.p0, tj.p1) < ti.ro + tj.ro:
                neigh[i].append(j)
    return neigh


def iter_fem_analytic_solids(
    part,
    *,
    angle_tol: float = 30.0,
    min_patch_quads: int = 12,
    ndigits: int = 6,
    joint_csg: bool = True,
    cut_margin: float = 2.0,
    reconstruct_curved: bool = True,
):
    """Yield ``(id, ada.geom)`` for a FEM shell mesh as analytic SOLIDS: each detected tube becomes
    a hollow annular member with its real wall thickness, and (``joint_csg``) each joint is resolved
    with boolean CSG the way a tubular joint actually works — the **chord** (the larger member) stays
    continuous and each **brace** (the smaller member meeting it) is saddle-cut to the chord's outer
    surface. This is asymmetric on purpose: cutting the chord with every brace too would punch a
    through-channel across it per brace, and several of those intersecting inside one chord produced
    the shredded-interior artifact.

    Walls are composed as ``outer_solid − inner_solid`` and every boolean operand is a filled
    cylinder (Manifold rejects the winding of a hollow profile-with-void extrusion). The chord
    cutting tool is extended ``cut_margin × radius`` past its ends so it fully trims the brace.

    Non-tube geometry (flat plates, curved panels) is emitted as analytic faces via
    :func:`iter_fem_analytic_faces` with the cylinders skipped and any facet lying on a tube wall
    dropped (so triangles that sat on the tube surface don't float on the solid)."""
    from ada.geom.booleans import BooleanResult, BoolOpEnum

    tubes = _collect_tubes(part, angle_tol, min_patch_quads, ndigits)
    neigh = _tube_neighbors(tubes) if joint_csg else {i: [] for i in range(len(tubes))}
    diff = BoolOpEnum.DIFFERENCE

    def _is_brace_of(i: int, j: int) -> bool:
        # member i is the brace (gets cut) w.r.t. neighbour j (the chord) when j is the larger tube;
        # equal radii (X-joint) → deterministic single cut by lower index so only one side is cut.
        ri, rj = tubes[i].ro, tubes[j].ro
        if rj > ri + 1e-9:
            return True
        if rj < ri - 1e-9:
            return False
        return j < i

    for i, tb in enumerate(tubes):
        cuts = [j for j in neigh[i] if _is_brace_of(i, j)]
        if not cuts:
            # uncut member → clean hollow profile-with-void extrusion (no boolean, no artifacts)
            yield (f"tube_{i}", _hollow_extrusion(tb.origin, tb.axis, tb.e1, tb.ro, tb.ri, tb.z0, tb.z1))
            continue
        # cut member → wall as outer − inner (boolean minuend must be a filled solid), then saddle
        # cuts. The inner (bore) cylinder is extended past BOTH caps: sharing exact cap planes with
        # the outer leaves Manifold unable to open the bore, capping it with a disk membrane across
        # the tube end (the "out-of-place tris at the ends"); punching through gives clean annuli.
        eps = 0.02 * (tb.z1 - tb.z0) + 1e-3
        geom = BooleanResult(
            _filled_cylinder(tb.origin, tb.axis, tb.e1, tb.ro, tb.z0, tb.z1),
            _filled_cylinder(tb.origin, tb.axis, tb.e1, tb.ri, tb.z0 - eps, tb.z1 + eps),
            diff,
        )
        for j in cuts:
            nb = tubes[j]
            margin = max(cut_margin * nb.ro, 1.0)
            tool = _filled_cylinder(nb.origin, nb.axis, nb.e1, nb.ro, nb.z0 - margin, nb.z1 + margin)
            geom = BooleanResult(geom, tool, diff)
        yield (f"tube_{i}", geom)

    # non-tube faces: skip cylinders (owned above) and drop facets lying on any tube wall
    def _on_tube(pt: np.ndarray) -> bool:
        for tb in tubes:
            d = pt - tb.origin
            ax = float(d @ tb.axis)
            if tb.z0 - 1e-6 <= ax <= tb.z1 + 1e-6:
                radial = float(np.linalg.norm(d - ax * tb.axis))
                if abs(radial - tb.r) <= tb.t:
                    return True
        return False

    for face in iter_fem_analytic_faces(
        part,
        angle_tol=angle_tol,
        min_patch_quads=min_patch_quads,
        ndigits=ndigits,
        reconstruct_curved=reconstruct_curved,
        skip_cylinders=True,
        drop_on_tube=(_on_tube if tubes else None),
    ):
        yield (f"face_{id(face)}", face)


def cylinder_trim_faces(prims: "_Primitives", patch: list[int], cf: "CylinderFit", ndigits: int = 6):
    """Trim the fitted cylinder by the patch's ACTUAL boundary (the joint-cut curves)
    instead of flat circles at the axial extremes: one CYLINDRICAL_SURFACE face bounded by
    the patch's boundary loops. Each edge carries a 3D curve (arc where consecutive nodes are
    at ~equal axial height, chord line otherwise) AND a 2D pcurve — the straight line in the
    cylinder's (u=angle, v=axial) parameter space between the two nodes. The pcurve is what
    puts the edge ON the cylinder (3D derived as surface(pcurve)) so BRepMesh tessellates the
    face curved even where the 3D edge is a chord, and it carries the periodic seam through
    unwrapped u. Returns a list of AdvancedFace, or None if the boundary won't resolve."""
    import math

    from ada.core.vector_utils import boundary_cycles_3d
    from ada.geom.curves import Circle, EdgeCurve, EdgeLoop, Line, OrientedEdge, Pcurve2dBSpline
    from ada.geom.direction import Direction
    from ada.geom.placement import Axis2Placement3D
    from ada.geom.points import Point
    from ada.geom.surfaces import AdvancedFace, CylindricalSurface, FaceBound

    cycles = boundary_cycles_3d([prims.outline(j) for j in patch], ndigits=ndigits)
    if not cycles:
        return None
    axis_d = Direction(*cf.axis)
    ref_d = Direction(*cf.e1)
    e2 = np.cross(cf.axis, cf.e1)  # so (u=angle from e1, v=axial) matches Geom_CylindricalSurface
    z_tol = 1e-6 * max(cf.z1 - cf.z0, cf.radius, 1.0)

    def _uv(p):
        d = np.asarray(p, dtype=float) - cf.origin
        return math.atan2(float(d @ e2), float(d @ cf.e1)), float(d @ cf.axis)

    def _pcurve(ua, ub):  # degree-1 (line) pcurve in (u, v) between two nodes
        return Pcurve2dBSpline(
            degree=1, control_points_2d=[list(ua), list(ub)], knots=[0.0, 1.0], knot_multiplicities=[2, 2]
        )

    def _edge(pa, pb):
        Pa, Pb = Point(*pa), Point(*pb)
        va, vb = (np.asarray(pa, float) - cf.origin) @ cf.axis, (np.asarray(pb, float) - cf.origin) @ cf.axis
        if abs(va - vb) <= z_tol:  # constant-height → circular arc on the cylinder
            loc = Point(*(cf.origin + float(va) * cf.axis))
            g = Circle(position=Axis2Placement3D(location=loc, axis=axis_d, ref_direction=ref_d), radius=cf.radius)
        else:
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
        # unwrap u continuously along the loop so pcurve endpoints connect across the seam
        us, vs = [], []
        prev_u = None
        for p in cyc:
            u, v = _uv(p)
            if prev_u is not None:
                u += 2.0 * math.pi * round((prev_u - u) / (2.0 * math.pi))
            us.append(u)
            vs.append(v)
            prev_u = u
        n = len(cyc)
        edges = []
        for i in range(n):
            j = (i + 1) % n
            ub = us[j] + (2.0 * math.pi * round((us[i] - us[j]) / (2.0 * math.pi)) if j == 0 else 0.0)
            oe = _edge(cyc[i], cyc[j])
            oe.pcurve = _pcurve((us[i], vs[i]), (ub, vs[j]))
            edges.append(oe)
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
