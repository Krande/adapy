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

from dataclasses import dataclass

import numpy as np

from ada.fem.capacity import extract
from ada.fem.results.common import Mesh

# QUAD4 corner natural coordinates, CCW (matches the connectivity node order).
_QUAD_CORNERS = ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0))

# NB: this module deliberately avoids ``@`` / ``np.dot`` / ``np.linalg.*``. Those
# dispatch to native BLAS/LAPACK, which crashes (Windows SEH 0xC06D007F) on some
# Sesam-env BLAS backends when called per element across a large model. All the
# operands here are tiny (2x2 Jacobian, 3-vectors), so closed-form scalar /
# elementwise arithmetic is both crash-safe and faster.


def _norm(v: np.ndarray) -> float:
    return float(np.sqrt((v * v).sum()))


def _dot1(a: np.ndarray, b: np.ndarray) -> float:
    return float((a * b).sum())


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
        if _norm(ex) > 0 and _norm(ez) > 0:
            ex = ex / _norm(ex)
            ez = ez / _norm(ez)
            ey = np.cross(ez, ex)
            ny = _norm(ey)
            if ny > 0:
                return ex, ey / ny
    ex = coords[1] - coords[0]
    ez = np.cross(coords[1] - coords[0], coords[2] - coords[0])
    ex = ex / (_norm(ex) or 1.0)
    ez = ez / (_norm(ez) or 1.0)
    ey = np.cross(ez, ex)
    return ex, ey / (_norm(ey) or 1.0)


def _plane_stress(eps: np.ndarray, E: float, nu: float) -> np.ndarray:
    """Plane-stress membrane stress (sxx, syy, txy) from strain (exx, eyy, gxy)."""
    f = E / (1.0 - nu * nu)
    exx, eyy, gxy = float(eps[0]), float(eps[1]), float(eps[2])
    return np.array([f * (exx + nu * eyy), f * (nu * exx + eyy), f * (1.0 - nu) / 2.0 * gxy])


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
    return np.array([_dot1(b, u), _dot1(c, v), _dot1(c, u) + _dot1(b, v)])


def _quad_strain_at(xy: np.ndarray, uv: np.ndarray, xi: float, eta: float) -> np.ndarray | None:
    """Bilinear quad membrane strain at natural coords (xi, eta). ``xy``/``uv`` (4, 2)."""
    dn_dxi = np.array([-(1 - eta), (1 - eta), (1 + eta), -(1 + eta)]) / 4.0
    dn_deta = np.array([-(1 - xi), -(1 + xi), (1 + xi), (1 - xi)]) / 4.0
    # 2x2 Jacobian, closed-form determinant + inverse (no LAPACK).
    j11 = _dot1(dn_dxi, xy[:, 0])
    j12 = _dot1(dn_dxi, xy[:, 1])
    j21 = _dot1(dn_deta, xy[:, 0])
    j22 = _dot1(dn_deta, xy[:, 1])
    det = j11 * j22 - j12 * j21
    if abs(det) < 1e-20:
        return None
    # inv(J) = 1/det [[j22, -j12], [-j21, j11]]  ->  d/dx, d/dy rows
    dn_dx = (j22 * dn_dxi - j12 * dn_deta) / det
    dn_dy = (-j21 * dn_dxi + j11 * dn_deta) / det
    u = uv[:, 0]
    v = uv[:, 1]
    return np.array([_dot1(dn_dx, u), _dot1(dn_dy, v), _dot1(dn_dy, u) + _dot1(dn_dx, v)])


@dataclass
class _RecoveryOp:
    """Step-invariant displacement→corner-stress operator for one shell element.

    The element frame, the planar node coordinates, and the shape-function
    derivative operators (tri ``b``/``c`` or per-corner quad ``dn_dx``/``dn_dy``)
    depend only on geometry, so they are built once and cached on the mesh.
    Recovery for a result case is then just projecting that case's nodal
    displacements and applying the cached operators — no Jacobian inverse, frame
    construction, or coordinate projection per case.
    """

    node_ids: list[int]
    corner_coords: list[np.ndarray]
    ex: np.ndarray
    ey: np.ndarray
    E: float
    nu: float
    is_tri: bool
    b: np.ndarray | None = None
    c: np.ndarray | None = None
    dn_dx: list[np.ndarray] | None = None
    dn_dy: list[np.ndarray] | None = None


def _build_recovery_op(
    mesh: Mesh,
    element_id: int,
    E: float,
    nu: float,
    transform: np.ndarray | None,
) -> _RecoveryOp | None:
    """Build the cached recovery operator, or ``None`` for unsupported shapes /
    degenerate geometry (the element then resolves to ``[]`` every case)."""
    node_ids = extract.element_node_ids(mesh, element_id)
    if len(node_ids) not in (3, 4):
        return None
    coords = extract.element_node_coords(mesh, element_id)
    if len(coords) != len(node_ids):
        return None

    ex, ey = _element_frame(coords, transform)
    origin = coords[0]
    rel = coords - origin
    xy = np.column_stack([(rel * ex).sum(axis=1), (rel * ey).sum(axis=1)])

    if len(node_ids) == 3:
        (x1, y1), (x2, y2), (x3, y3) = xy
        two_a = (x2 - x1) * (y3 - y1) - (x3 - x1) * (y2 - y1)
        if abs(two_a) < 1e-20:
            return None
        b = np.array([y2 - y3, y3 - y1, y1 - y2]) / two_a
        c = np.array([x3 - x2, x1 - x3, x2 - x1]) / two_a
        return _RecoveryOp(node_ids, [coords[i] for i in range(3)], ex, ey, E, nu, True, b=b, c=c)

    dn_dx_l: list[np.ndarray] = []
    dn_dy_l: list[np.ndarray] = []
    corner_coords: list[np.ndarray] = []
    for i, (xi, eta) in enumerate(_QUAD_CORNERS):
        dn_dxi = np.array([-(1 - eta), (1 - eta), (1 + eta), -(1 + eta)]) / 4.0
        dn_deta = np.array([-(1 - xi), -(1 + xi), (1 + xi), (1 - xi)]) / 4.0
        j11 = _dot1(dn_dxi, xy[:, 0])
        j12 = _dot1(dn_dxi, xy[:, 1])
        j21 = _dot1(dn_deta, xy[:, 0])
        j22 = _dot1(dn_deta, xy[:, 1])
        det = j11 * j22 - j12 * j21
        if abs(det) < 1e-20:
            return None
        dn_dx_l.append((j22 * dn_dxi - j12 * dn_deta) / det)
        dn_dy_l.append((-j21 * dn_dxi + j11 * dn_deta) / det)
        corner_coords.append(coords[i])
    return _RecoveryOp(node_ids, corner_coords, ex, ey, E, nu, False, dn_dx=dn_dx_l, dn_dy=dn_dy_l)


def _apply_recovery_op(op: _RecoveryOp, disp_by_node: dict[int, np.ndarray]):
    try:
        disp = np.array([disp_by_node[n][:3] for n in op.node_ids], dtype=float)
    except KeyError:
        return []
    u = (disp * op.ex).sum(axis=1)
    v = (disp * op.ey).sum(axis=1)
    if op.is_tri:
        strain = np.array([_dot1(op.b, u), _dot1(op.c, v), _dot1(op.c, u) + _dot1(op.b, v)])
        tensor = _plane_stress(strain, op.E, op.nu)  # constant over a CST
        return [(op.corner_coords[i], tensor) for i in range(3)]
    out: list[tuple[np.ndarray, np.ndarray]] = []
    for i in range(4):
        dn_dx = op.dn_dx[i]
        dn_dy = op.dn_dy[i]
        strain = np.array([_dot1(dn_dx, u), _dot1(dn_dy, v), _dot1(dn_dy, u) + _dot1(dn_dx, v)])
        out.append((op.corner_coords[i], _plane_stress(strain, op.E, op.nu)))
    return out


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
    displacements. The step-invariant operator is built once and cached on the
    mesh, so repeated cases only project displacements (see :class:`_RecoveryOp`).
    """
    cache = getattr(mesh, "_cap_recovery_ops", None)
    if cache is None:
        cache = {}
        try:
            mesh._cap_recovery_ops = cache  # type: ignore[attr-defined]
        except Exception:
            pass
    if element_id in cache:
        op = cache[element_id]
    else:
        op = _build_recovery_op(mesh, element_id, E, nu, transform)
        cache[element_id] = op
    if op is None:
        return []
    return _apply_recovery_op(op, disp_by_node)


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
