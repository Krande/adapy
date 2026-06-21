"""Recover shell element membrane stresses from nodal displacements.

Some Sesam SIN files are written without element stresses (SESTRA ``RSEL
ISEL4=-1`` — the "smart load combination" / forces-only workflow), and often
without shell force resultants too, leaving only nodal displacements for the
plate (shell) elements. The DNV-RP-C201 stiffened-plate check still needs the
plate membrane stresses, so we reconstruct them the way SESTRA's postprocessing
pass does: from the element's nodal in-plane displacements,

    eps = B(xi, eta) . u_local      sigma = D . eps   (plane stress, membrane)

for 3-node (TRI) and 4-node (QUAD) shells. Only the *membrane* part is recovered
(translational DOF); bending (rotations/curvature) is not needed by the
membrane-based capacity check.

Membrane stress is evaluated **at each element corner** (not just the centre) so
the resolver keeps the along-span stress gradient it relies on for the Section-5
start/mid/end station values — recovering only a single centroid value flattens
that gradient and underestimates the governing utilisations.

Each returned tensor ``(sigma_xx, sigma_yy, tau_xy)`` is in the element local
frame :func:`ada.fem.capacity.stress_resolve._rotate_to_stiffener_frame`
assumes — ``ex`` = ``transform`` row 0 (else the first element edge), ``ez`` the
element normal, ``ey = ez x ex`` — tension-positive like ``RVSTRESS``.
"""

from __future__ import annotations

import numpy as np

from ada.fem.capacity import extract
from ada.fem.results.common import Mesh

# QUAD4 corner natural coordinates, CCW (matches the connectivity node order).
_QUAD_CORNERS = ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0))


def _element_frame(coords: np.ndarray, transform: np.ndarray | None) -> tuple[np.ndarray, np.ndarray]:
    """Return orthonormal (ex, ey) of the element membrane plane.

    Mirrors ``_rotate_to_stiffener_frame``: use the supplied Sesam element basis
    when available (row 0 = local x, row 2 = normal), else fall back to the first
    element edge and the triangle normal.
    """
    if transform is not None and np.shape(transform) == (3, 3):
        basis = np.asarray(transform, dtype=float)
        ex = basis[0]
        ez = basis[2]
        if np.linalg.norm(ex) > 0 and np.linalg.norm(ez) > 0:
            ex = ex / np.linalg.norm(ex)
            ez = ez / np.linalg.norm(ez)
            ey = np.cross(ez, ex)
            ny = np.linalg.norm(ey)
            if ny > 0:
                return ex, ey / ny
    ex = coords[1] - coords[0]
    ez = np.cross(coords[1] - coords[0], coords[2] - coords[0])
    ex = ex / (np.linalg.norm(ex) or 1.0)
    ez = ez / (np.linalg.norm(ez) or 1.0)
    ey = np.cross(ez, ex)
    return ex, ey / (np.linalg.norm(ey) or 1.0)


def _plane_stress_matrix(E: float, nu: float) -> np.ndarray:
    f = E / (1.0 - nu * nu)
    return f * np.array([[1.0, nu, 0.0], [nu, 1.0, 0.0], [0.0, 0.0, (1.0 - nu) / 2.0]])


def _tri_strain(xy: np.ndarray, uv: np.ndarray) -> np.ndarray | None:
    """Constant membrane strain of a 3-node triangle. ``xy``/``uv`` are (3, 2)."""
    (x1, y1), (x2, y2), (x3, y3) = xy
    two_a = (x2 - x1) * (y3 - y1) - (x3 - x1) * (y2 - y1)
    if abs(two_a) < 1e-20:
        return None
    b = np.array([y2 - y3, y3 - y1, y1 - y2]) / two_a
    c = np.array([x3 - x2, x1 - x3, x2 - x1]) / two_a
    u = uv[:, 0]
    v = uv[:, 1]
    return np.array([float(b @ u), float(c @ v), float(c @ u + b @ v)])


def _quad_strain_at(xy: np.ndarray, uv: np.ndarray, xi: float, eta: float) -> np.ndarray | None:
    """Bilinear quad membrane strain at natural coords (xi, eta). ``xy``/``uv`` (4, 2)."""
    dn_dxi = np.array([-(1 - eta), (1 - eta), (1 + eta), -(1 + eta)]) / 4.0
    dn_deta = np.array([-(1 - xi), -(1 + xi), (1 + xi), (1 - xi)]) / 4.0
    jac = np.array([[dn_dxi @ xy[:, 0], dn_dxi @ xy[:, 1]], [dn_deta @ xy[:, 0], dn_deta @ xy[:, 1]]])
    det = np.linalg.det(jac)
    if abs(det) < 1e-20:
        return None
    dn = np.linalg.inv(jac) @ np.vstack([dn_dxi, dn_deta])  # (2, 4): d/dx, d/dy
    u = uv[:, 0]
    v = uv[:, 1]
    return np.array([float(dn[0] @ u), float(dn[1] @ v), float(dn[1] @ u + dn[0] @ v)])


def recover_membrane_corner_points(
    mesh: Mesh,
    element_id: int,
    disp_by_node: dict[int, np.ndarray],
    E: float,
    nu: float,
    transform: np.ndarray | None = None,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Per-corner ``(xyz, (sxx, syy, txy))`` membrane stress for one shell element.

    Returns ``[]`` for unsupported shapes, degenerate geometry, or missing nodal
    displacements.
    """
    node_ids = extract.element_node_ids(mesh, element_id)
    if len(node_ids) not in (3, 4):
        return []
    coords = extract.element_node_coords(mesh, element_id)
    if len(coords) != len(node_ids):
        return []
    try:
        disp = np.array([disp_by_node[n][:3] for n in node_ids], dtype=float)
    except KeyError:
        return []

    ex, ey = _element_frame(coords, transform)
    origin = coords[0]
    xy = np.column_stack([(coords - origin) @ ex, (coords - origin) @ ey])
    uv = np.column_stack([disp @ ex, disp @ ey])
    D = _plane_stress_matrix(E, nu)

    out: list[tuple[np.ndarray, np.ndarray]] = []
    if len(node_ids) == 3:
        strain = _tri_strain(xy, uv)
        if strain is None:
            return []
        tensor = D @ strain  # constant over a CST
        return [(coords[i], tensor) for i in range(3)]
    for i, (xi, eta) in enumerate(_QUAD_CORNERS):
        strain = _quad_strain_at(xy, uv, xi, eta)
        if strain is None:
            return []
        out.append((coords[i], D @ strain))
    return out


def build_recovered_stress(
    mesh: Mesh,
    disp_by_node: dict[int, np.ndarray],
    material_by_element: dict[int, tuple[float, float]],
    step: int,
    transform_by_element: dict[int, np.ndarray] | None = None,
):
    """Synthetic ``STRESS`` block + result-point coords for a stress-less step.

    Each corner is emitted as a duplicated bottom/top pair (the membrane value on
    both surfaces) so the resolver's top/bottom pairing yields one membrane point
    per corner at the corner coordinate. Returns ``(ElementFieldData,
    coords_by_element)`` where ``coords_by_element[eid]`` maps point id → xyz, to
    merge into ``AuxRecords.result_point_coords_by_element``.
    """
    from ada.fem.results.field_data import ElementFieldData, FieldPosition

    transform_by_element = transform_by_element or {}
    rows: list[list[float]] = []
    coords_by_element: dict[int, dict[int, np.ndarray]] = {}
    for element_id, (E, nu) in material_by_element.items():
        pts = recover_membrane_corner_points(
            mesh, element_id, disp_by_node, E, nu, transform_by_element.get(element_id)
        )
        if not pts:
            continue
        n = len(pts)
        cmap: dict[int, np.ndarray] = {}
        for k, (xyz, tensor) in enumerate(pts):
            bot, top = k + 1, k + 1 + n
            rows.append([float(element_id), float(bot), float(tensor[0]), float(tensor[1]), float(tensor[2])])
            rows.append([float(element_id), float(top), float(tensor[0]), float(tensor[1]), float(tensor[2])])
            cmap[bot] = np.asarray(xyz, dtype=float)
            cmap[top] = np.asarray(xyz, dtype=float)
        coords_by_element[element_id] = cmap

    block = ElementFieldData(
        "STRESS",
        int(step),
        ["SIGXX", "SIGYY", "TAUXY"],
        np.array(rows, dtype=float) if rows else np.empty((0, 5), dtype=float),
        field_pos=FieldPosition.INT,
    )
    return block, coords_by_element


def displacements_by_node(disp_block) -> dict[int, np.ndarray]:
    """Map node id → translation 3-vector from an ``RVNODDIS`` field block.

    The block's ``values`` rows are ``[node_id, U1, U2, U3, U4, U5, U6]``.
    """
    out: dict[int, np.ndarray] = {}
    for row in disp_block.values:
        out[int(row[0])] = np.asarray(row[1:4], dtype=float)
    return out
