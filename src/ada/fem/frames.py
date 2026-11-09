"""Supports in a rotated frame: a ``Bc`` with a ``csys`` holds (or prescribes) its dofs along that system's axes.

A support on a skewed face -- a roller on a slope, a bearing turned 45 degrees -- holds the displacement normal to it
and leaves the one along it free, which no choice of global dofs says. ``Bc(..., csys=Csys(...))`` gives its ``dofs``
and ``magnitudes`` in the rectangular system ``csys`` (:meth:`ada.fem.common.Csys.axes`): dof 1 along local x, 4 about
it, and so on.

Many such frames are global axes in another order -- a 90 degree turn, or any turn about z for a support that holds
dz alone -- and then the support *is* a global one. :func:`in_global_axes` gives that one, so every writer can write
it as an ordinary support; only a frame that really mixes the global axes reaches a writer as a frame:

* **Sesam**: the node is transformed (BNDOF / BNTRCOS), and everything Sesam gives at that node is then in its frame
  (SIF 7.2.17, 7.2.23: "refer to the transformed co-ordinate system if the node NODENO is transformed") --
  :mod:`ada.fem.formats.sesam.write.node_frames`.
* **Code_Aster**: ``LIAISON_OBLIQUE`` (U4.44.01), the dofs along the axes its nautical angles turn to
  (:func:`nautical_angles`).

A rule for one group of three dofs (the translations, or the rotations): the local axes holding one condition span a
subspace, and it is spanned by global axes exactly when its projector has a 0/1 diagonal; then each global axis with
a 1 takes that condition. (The projector is then diagonal too: ``P = P^T P`` makes ``P_jj`` the sum of row ``j``'s
squares.) A *prescribed* value is a displacement along its own local axis, so it maps only when that axis is a global
one, ``+-e_j``, and takes the sign.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from ada.fem import Bc

#: How far a direction cosine may be from the 0 / 1 it stands for (GeniE writes ten significant digits).
COSINE_TOL = 1e-6


def bc_axes(bc: Bc) -> np.ndarray | None:
    """The rows of ``bc``'s local axes in global components, or None when it is in global axes (no ``csys``, or the
    identity)."""
    csys = getattr(bc, "csys", None)
    if csys is None:
        return None
    axes = csys.axes()
    if axes is None:
        raise ValueError(f"Bc {bc.name!r}: its csys is not a rectangular system given by coordinates")
    if np.allclose(axes, np.eye(3), atol=COSINE_TOL):
        return None
    return axes


def _is_encastre(dofs) -> bool:
    return any(isinstance(d, str) and d.lower() == "encastre" for d in dofs)


def _map_group(axes: np.ndarray, conditions: list) -> list | None:
    """Three local conditions (``None`` free, ``0.0`` held, else a prescribed value) on the global axes, or None
    when the frame mixes them (see the module docstring)."""
    out: list = [None] * 3
    held = [i for i, c in enumerate(conditions) if c is not None and c == 0.0]
    if held:
        sub = axes[held]
        diagonal = np.diag(sub.T @ sub)
        if not np.allclose(diagonal, np.round(diagonal), atol=COSINE_TOL):
            return None
        for j in np.flatnonzero(np.round(diagonal)):
            out[j] = 0.0
    for i, c in enumerate(conditions):
        if c is None or c == 0.0:
            continue
        row = axes[i]
        j = int(np.argmax(np.abs(row)))
        if not np.isclose(abs(row[j]), 1.0, atol=COSINE_TOL) or out[j] is not None:
            return None
        out[j] = float(np.sign(row[j])) * float(c)
    return out


def in_global_axes(bc: Bc) -> Bc | None:
    """``bc`` itself when it is in global axes; a copy of it in global axes when its frame maps onto them (a 90
    degree turn, a lone dz under a turn about z); None when it does not -- the writer then needs a frame.

    The copy is not added to the model: it is what a writer writes in place of ``bc``.
    """
    axes = bc_axes(bc)
    if axes is None:
        return bc
    dofs = list(bc.dofs)
    if _is_encastre(dofs):
        return _global_copy(bc, [1, 2, 3, 4, 5, 6], None)
    magnitudes = list(bc.magnitudes or [None] * len(dofs))
    local: list = [None] * 6
    for d, m in zip(dofs, magnitudes):
        if d is None:
            continue
        local[int(d) - 1] = 0.0 if m is None else float(m)
    mapped = []
    for group in (local[:3], local[3:]):
        g = _map_group(axes, group) if any(c is not None for c in group) else [None] * 3
        if g is None:
            return None
        mapped += g
    new_dofs = [i + 1 for i, c in enumerate(mapped) if c is not None]
    if bc.magnitudes is None or all(m is None for m in bc.magnitudes):
        return _global_copy(bc, new_dofs, None)
    return _global_copy(bc, new_dofs, [mapped[d - 1] for d in new_dofs])


def _global_copy(bc: Bc, dofs: list[int], magnitudes: list | None) -> Bc:
    out = copy.copy(bc)
    out._dofs = dofs
    out._magnitudes = [None] * len(dofs) if magnitudes is None else magnitudes
    out._csys = None
    return out


def local_conditions(bc: Bc) -> list:
    """The six conditions of ``bc`` in its own axes: ``None`` free, ``0.0`` held, else the prescribed value."""
    dofs = list(bc.dofs)
    if _is_encastre(dofs):
        return [0.0] * 6
    out: list = [None] * 6
    for d, m in zip(dofs, list(bc.magnitudes or [None] * len(dofs))):
        if d is not None:
            out[int(d) - 1] = 0.0 if m is None else float(m)
    return out


def nautical_angles(axes: np.ndarray) -> tuple[float, float, float]:
    """Code_Aster's ``ANGL_NAUT`` (degrees) of the frame whose rows are ``axes``.

    Code_Aster's global-to-local matrix of angles ``(a, b, g)`` (``matrot``) has the rows ``x' = (cb ca, cb sa,
    -sb)``, ``y' = (sg sb ca - cg sa, cg ca + sg sb sa, sg cb)`` and ``z' = (sg sa + cg sb ca, cg sb sa - ca sg,
    cg cb)``: a turn ``a`` about z, then ``b`` about the new y (positive tilting x' *down*), then ``g`` about the new
    x. Inverted here, and checked by rebuilding the matrix.
    """
    r = np.asarray(axes, dtype=float)
    sb = float(np.clip(-r[0, 2], -1.0, 1.0))
    b = np.arcsin(sb)
    cb = np.cos(b)
    if abs(cb) > 1e-9:
        a = np.arctan2(r[0, 1], r[0, 0])
        g = np.arctan2(r[1, 2], r[2, 2])
    else:  # x' along +-z: only a - g (or a + g) is set; take a = 0
        a = 0.0
        g = np.arctan2(r[1, 0] / sb, r[1, 1])
    angles = (float(np.degrees(a)), float(np.degrees(b)), float(np.degrees(g)))
    if not np.allclose(nautical_matrix(*angles), r, atol=1e-9):
        raise ValueError(f"the axes {r.tolist()} are not a right-handed orthonormal frame")
    return angles


def nautical_matrix(a: float, b: float, g: float) -> np.ndarray:
    """Code_Aster's ``matrot`` for nautical angles in degrees: rows the local axes in global components."""
    a, b, g = np.radians([a, b, g])
    ca, sa, cb, sb, cg, sg = np.cos(a), np.sin(a), np.cos(b), np.sin(b), np.cos(g), np.sin(g)
    return np.array(
        [
            [cb * ca, cb * sa, -sb],
            [sg * sb * ca - cg * sa, cg * ca + sg * sb * sa, sg * cb],
            [sg * sa + cg * sb * ca, cg * sb * sa - ca * sg, cg * cb],
        ]
    )


def in_global_axes_or_refuse(bcs, writer: str) -> list:
    """``bcs`` in global axes, for a writer that writes no rotated frame (Abaqus, CalculiX, OpenCourant): a frame that
    maps onto the global axes is written as the global support it is; one that mixes them is left out and reported
    by name (``omitted``) -- never written along the wrong axes, and never failing the rest of the deck (a GeniE
    model with one sloped support curve has every other support to write)."""
    from ada.fem.formats import conversion_report

    out = []
    for bc in bcs:
        g = in_global_axes(bc)
        if g is None:
            conversion_report.current().omitted(
                f"{writer} writer",
                "Bc",
                bc.name,
                "a support in a rotated frame that mixes the global axes; this writer writes supports in global axes "
                "only (the Sesam and Code_Aster writers take it), so it is not written",
                csys=bc.csys.name,
            )
            continue
        out.append(g)
    return out
