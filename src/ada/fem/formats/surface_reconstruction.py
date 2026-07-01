"""Reconstruct B-spline surface panels from FEM shell elements.

``create_objects_from_fem`` produces one flat ``Plate`` per shell element. For a
mesh generated from curved B-spline panels (ship hulls, offshore jackets) that is
tens of thousands of tiny non-coplanar plates that ``merge_coplanar_plates`` can
barely collapse — and a STEP export of one solid per plate is huge and slow.

``reconstruct_shell_surfaces`` instead recovers each smooth, structured quad panel
as a single NURBS surface (a :class:`~ada.api.plates.base_pl.PlateCurved`), so one
curved plate replaces hundreds of flat ones.

**Opt-in and strictly safe**: only clean, topologically-rectangular quad patches
are reconstructed. Triangles, holes/cutouts, folds, T-junctions, and any patch the
grid recovery or surface fit can't handle cleanly fall back to today's flat-plate
path (optionally still coplanar-merged). Worst case is "no reconstruction", never
corrupt geometry.

All CAD-kernel work (the grid→NURBS fit) goes through the backend abstraction
(``ada.cad.active_backend().build_bspline_advanced_face_from_grid``), which returns
a backend-neutral ``ada.geom`` ``AdvancedFace`` — **no OCC object is ever held by a
reconstructed plate**. Backends without a fit (adacpp) raise ``NotImplementedError``
and every patch falls back.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

import numpy as np

from ada.config import logger
from ada.fem.formats.concept_merge import _edge_key, _round_key
from ada.fem.shapes.definitions import ShellShapes

if TYPE_CHECKING:
    from ada import Part, Plate
    from ada.api.plates.base_pl import PlateCurved
    from ada.fem.elements import Elem

# QUAD-family corners are the first four nodes (gmsh ordering); mid-edge / centre
# nodes of QUAD8/QUAD9 are ignored for the corner grid.
_QUAD_TYPES = {ShellShapes.QUAD, ShellShapes.QUAD8, ShellShapes.QUAD9}
# Unit-cell corner offsets for ring order [c0, c1, c2, c3].
_CELL = ((0, 0), (1, 0), (1, 1), (0, 1))


def reconstruct_shell_surfaces(
    part: "Part",
    ndigits: int = 6,
    angle_tol: float = 30.0,
    max_dev: float | None = None,
    merge_fallback: bool = True,
    min_patch_quads: int = 12,
) -> "list[PlateCurved | Plate]":
    """Return concept objects for ``part``'s shell mesh, reconstructing smooth
    structured quad panels as curved plates and falling back to flat plates for
    everything else.

    ``angle_tol`` (degrees) bounds normal discontinuity within a smooth patch
    (region growing stops at folds). ``max_dev`` is the absolute max allowed
    deviation of the fitted surface from the nodes; ``None`` derives it per patch
    from the patch size. ``merge_fallback`` runs ``merge_coplanar_plates`` on the
    leftover flat plates.
    """
    shells = list(part.fem.elements.shell)
    quad_buckets: dict[tuple, list["Elem"]] = {}
    fallback_elems: list["Elem"] = []
    for el in shells:
        if el.type in _QUAD_TYPES:
            key = (el.fem_sec.material.name, round(float(el.fem_sec.thickness), ndigits))
            quad_buckets.setdefault(key, []).append(el)
        else:
            fallback_elems.append(el)

    reconstructed: list = []
    leftover: list["Elem"] = list(fallback_elems)
    for quads in quad_buckets.values():
        plates, unused = _reconstruct_bucket(quads, part, ndigits, angle_tol, max_dev, min_patch_quads)
        reconstructed.extend(plates)
        leftover.extend(unused)

    flat = _fallback_plates(leftover, part, merge_fallback, ndigits)
    logger.info(
        f"surface reconstruction: {len(reconstructed)} curved panels + {len(flat)} flat plates "
        f"from {len(shells)} shell elements"
    )
    return reconstructed + flat


# ── reconstruction per (material, thickness) bucket ─────────────────────────


def _reconstruct_bucket(quads, part, ndigits, angle_tol, max_dev, min_patch_quads):
    """Region-grow smooth structured quad patches and fit each; return
    (list[PlateCurved], list[Elem] that were not reconstructed)."""
    from ada.cad import active_backend

    corners = [[q.nodes[i] for i in range(4)] for q in quads]  # 4 corner Node objs
    keys = [tuple(_round_key(n.p, ndigits) for n in cs) for cs in corners]  # ring keys
    normals = [_quad_normal([n.p for n in cs]) for cs in corners]

    # edge_key -> [quad indices sharing it]
    edge_owner: dict[tuple, list[int]] = {}
    for qi, ring in enumerate(keys):
        for a, b in zip(ring, ring[1:] + ring[:1]):
            edge_owner.setdefault(_edge_key(a, b), []).append(qi)

    cos_tol = float(np.cos(np.deg2rad(angle_tol)))
    visited = [False] * len(quads)
    out_plates: list = []
    unused: list["Elem"] = []

    for seed in range(len(quads)):
        if visited[seed]:
            continue
        comp, coord, ok = _grow_grid(seed, keys, normals, edge_owner, cos_tol, visited)
        # A NURBS B-rep solid is much heavier than a few flat plates — only
        # reconstruct patches big enough to pay for themselves; smaller ones go
        # to the (coplanar-merging) flat fallback.
        if not ok or len(comp) < min_patch_quads:
            unused.extend(quads[i] for i in comp)
            continue
        grid = _grid_from_coords(coord, keys, corners, comp)
        if grid is None:
            unused.extend(quads[i] for i in comp)
            continue
        plate = _fit_patch(grid, quads, comp, part, max_dev, active_backend())
        if plate is None:
            unused.extend(quads[i] for i in comp)
        else:
            out_plates.append(plate)
    return out_plates, unused


def _grow_grid(seed, keys, normals, edge_owner, cos_tol, visited):
    """BFS from ``seed`` assigning integer (gi, gj) grid coords to corner-node
    keys, crossing only smooth (normal within tol) manifold (degree-2) edges.

    Returns (component_quad_indices, {node_key: (gi, gj)}, ok). ``ok`` is False on
    any topological conflict (the component is then not a clean grid → fallback).
    """
    coord: dict[tuple, tuple[int, int]] = {}
    ring0 = keys[seed]
    for k, off in zip(ring0, _CELL):
        coord[k] = off
    comp = [seed]
    visited[seed] = True
    q = deque([seed])
    ok = True
    while q:
        qi = q.popleft()
        ring = keys[qi]
        for e in range(4):
            a, b = ring[e], ring[(e + 1) % 4]
            owners = edge_owner.get(_edge_key(a, b), ())
            if len(owners) != 2:  # boundary or non-manifold (T-junction)
                continue
            nb = owners[0] if owners[1] == qi else owners[1]
            # smoothness: stop at folds
            if abs(float(np.dot(normals[qi], normals[nb]))) < cos_tol:
                continue
            # perpendicular grid step into the placed quad: corner before a.
            prev_a = ring[(e - 1) % 4]
            qx = coord[prev_a][0] - coord[a][0]
            qy = coord[prev_a][1] - coord[a][1]
            new_a = (coord[a][0] - qx, coord[a][1] - qy)
            new_b = (coord[b][0] - qx, coord[b][1] - qy)
            # neighbour ring: a, b are consecutive; assign their *other* neighbours.
            nbring = keys[nb]
            try:
                ia, ib = nbring.index(a), nbring.index(b)
            except ValueError:
                ok = False
                continue
            if ib == (ia + 1) % 4:
                ka, kb = nbring[(ia - 1) % 4], nbring[(ib + 1) % 4]
            elif ib == (ia - 1) % 4:
                ka, kb = nbring[(ia + 1) % 4], nbring[(ib - 1) % 4]
            else:
                ok = False
                continue
            for k, c in ((ka, new_a), (kb, new_b)):
                if k in coord:
                    if coord[k] != c:
                        ok = False  # conflicting placement → not a clean grid
                else:
                    coord[k] = c
            if not visited[nb]:
                visited[nb] = True
                comp.append(nb)
                q.append(nb)
    return comp, coord, ok


def _grid_from_coords(coord, keys, corners, comp):
    """Validate the placed component is a full (nu × nv) rectangle and build the
    node-position grid. Returns ``list[list[(x, y, z)]]`` or ``None``."""
    if not coord:
        return None
    gis = [c[0] for c in coord.values()]
    gjs = [c[1] for c in coord.values()]
    gi0, gj0 = min(gis), min(gjs)
    nu = max(gis) - gi0 + 1
    nv = max(gjs) - gj0 + 1
    if nu < 2 or nv < 2:
        return None
    # one node per cell, full rectangle, and the right number of quads
    if len(coord) != nu * nv or len(comp) != (nu - 1) * (nv - 1):
        return None
    # node-key -> position (corner nodes carry the coords)
    pos: dict[tuple, tuple] = {}
    for qi in comp:
        for k, n in zip(keys[qi], corners[qi]):
            pos[k] = tuple(float(x) for x in n.p)
    grid = [[None] * nv for _ in range(nu)]
    for k, (gi, gj) in coord.items():
        if k not in pos:
            return None
        grid[gi - gi0][gj - gj0] = pos[k]
    if any(grid[i][j] is None for i in range(nu) for j in range(nv)):
        return None
    return grid


def _fit_patch(grid, quads, comp, part, max_dev, backend):
    from ada.api.plates.base_pl import PlateCurved
    from ada.core.guid import create_guid
    from ada.geom import Geometry

    tol = max_dev if max_dev is not None else _derive_tol(grid)
    try:
        advanced_face = backend.build_bspline_advanced_face_from_grid(grid, tol)
    except NotImplementedError:
        # Backend has no NURBS fit (adacpp / OCC absent): use the OCC-free native degree-1
        # tensor B-spline (control points = grid nodes → passes through every node exactly).
        from ada.fem.formats.mesh_faces import _bspline_surface_face_from_grid

        advanced_face = _bspline_surface_face_from_grid(grid)
    except Exception as ex:  # defensive: any fit failure → fallback
        logger.debug(f"surface fit raised, falling back to flat plates: {ex}")
        return None
    if advanced_face is None:
        return None

    ref = quads[comp[0]]
    fem_sec = ref.fem_sec
    mat = part.materials.add(fem_sec.material.copy_to(fem_sec.material.name, parent=part))
    try:
        return PlateCurved(
            f"recon_sh{ref.id}",
            Geometry(create_guid(), advanced_face, None),
            float(fem_sec.thickness),
            mat=mat,
            extrude_as_solid=True,
            parent=part,
        )
    except Exception as ex:
        logger.debug(f"PlateCurved construction failed, falling back: {ex}")
        return None


def _derive_tol(grid) -> float:
    pts = np.array([p for row in grid for p in row], dtype=float)
    diag = float(np.linalg.norm(pts.max(axis=0) - pts.min(axis=0)))
    return max(diag * 1e-3, 1e-6)


def _quad_normal(pts) -> np.ndarray:
    p0, p1, p2, p3 = (np.asarray(p, dtype=float) for p in pts)
    n = np.cross(p2 - p0, p3 - p1)  # diagonal cross — robust for planar quads
    nrm = float(np.linalg.norm(n))
    return n / nrm if nrm > 0 else n


# ── flat-plate fallback ─────────────────────────────────────────────────────


def _fallback_plates(elems, part, merge_fallback, ndigits):
    from ada.fem.formats.utils import convert_shell_elem_to_plates

    if not elems:
        return []
    mat_dict: dict = {}
    plates: list = []
    for el in elems:
        plates.extend(convert_shell_elem_to_plates(el, part, mat_dict))
    if merge_fallback and plates:
        from ada.fem.formats.concept_merge import merge_coplanar_plates

        plates = merge_coplanar_plates(plates, part, ndigits)
    return plates
