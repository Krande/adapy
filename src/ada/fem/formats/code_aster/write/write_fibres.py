"""Plastic beams as multi-fibre beams (``POU_D_EM``): a beam of a material with a hardening curve.

Code_Aster integrates a beam's plasticity over its section only on fibres (U3.11.07): each fibre an Euler beam of its
own area at its own place in the section, all held to the beam's plane sections. The section is given as point fibres
(``DEFI_GEOM_FIBRE`` ``FIBRE``: ``(x, y, area)`` triplets, U4.26.01), ``x`` the beam's local y and ``y`` its local z,
about the beam's axis -- the centroid, the axis the ``POUTRE`` ``GENERALE`` characteristics are given about. Each fibre
section has its own behaviour (``DEFI_COMPOR`` ``MULTIFIBRE``, U4.43.06): von Mises with isotropic hardening along the
material's ``TRACTION`` curve, the material's elasticity for the torsion (``MATER_SECT``); the material field gives it
to the beam's elements (``AFFE_MATERIAU`` ``AFFE_COMPOR``), and the nonlinear steps solve it as ``RELATION =
'MULTIFIBRE'``.

How a section is cut (:func:`section_fibres`):

* a section whose outline runs along its axes only (I, H, T, box, flat bar, channel, angle as adapy draws them): on the
  grid through its vertices, each interval cut further to at most :data:`FIBRE_SIZE` of the section's extent -- the
  cells are exactly the section, so the area is exact and the inertia short only by the cells' own (``b h^3 / 12``
  each);
* a tube or a round bar: annular sectors, each fibre at its sector's centroid, the area exact;
* any other outline: the cells of a fine grid whose centres lie inside it.

Code_Aster checks the fibres against ``POUTRE``'s ``A`` and ``IY``/``IZ`` (``PREC_AIRE`` 1 %, ``PREC_INERTIE`` 10 %).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from ada import Part
    from ada.fem import FemSection

#: The largest fibre, as a share of the section's extent in each direction: 40 across its width and its depth, so a
#: thin flat bar's weak axis is cut as finely as its strong one (each cell's own inertia lost, 1 / n^2 of a strip's).
FIBRE_SIZE = 1.0 / 40

#: Rings and sectors of a tube or a round bar.
TUBE_RINGS, TUBE_SECTORS = 4, 36


def fibre_beams(part: Part) -> list[FemSection]:
    """The beam sections solved as multi-fibre beams: a beam (not a truss or a cable) whose material has a hardening
    curve, in a model whose beams are not in large rotations (``POU_D_T_GD`` has no fibres) and not Timoshenko beams
    (``POU_D_EM`` is Euler-Bernoulli; the Timoshenko fibre beam ``POU_D_TGM`` takes one fibre group per element)."""
    from .steps.static_nonlin import large_rotation_beams
    from .write_materials import traction_curve

    if large_rotation_beams(part) or part.fem.options.CODE_ASTER.use_timoshenko_beams:
        return []
    return [
        fs
        for fs in part.fem.sections.lines
        if not fs.is_axial_only and not fs.is_cable and traction_curve(fs.material) is not None
    ]


def _inside(points: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """Even-odd rule: which of ``points`` (``(n, 2)``) lie inside the polygon ``poly`` (``(m, 2)``)."""
    x, y = points[:, 0], points[:, 1]
    inside = np.zeros(len(points), dtype=bool)
    for (x1, y1), (x2, y2) in zip(poly, np.roll(poly, -1, axis=0)):
        crosses = (y1 > y) != (y2 > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            xc = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
        inside ^= crosses & (x < xc)
    return inside


def _rectilinear(poly: np.ndarray) -> bool:
    d = np.roll(poly, -1, axis=0) - poly
    return bool(np.all((np.abs(d[:, 0]) < 1e-12) | (np.abs(d[:, 1]) < 1e-12)))


def _cuts(values, size: float) -> np.ndarray:
    """The sorted distinct ``values``, each interval between them cut into pieces no larger than ``size``."""
    v = np.unique(np.round(np.asarray(values, dtype=float), 12))
    out = [v[0]]
    for a, b in zip(v[:-1], v[1:]):
        n = max(1, int(np.ceil((b - a) / size - 1e-9)))
        out += list(np.linspace(a, b, n + 1)[1:])
    return np.asarray(out)


def _grid_fibres(outer: np.ndarray, inner: list[np.ndarray], exact: bool) -> np.ndarray:
    lo, hi = outer.min(axis=0), outer.max(axis=0)
    sx, sy = FIBRE_SIZE * float(hi[0] - lo[0]), FIBRE_SIZE * float(hi[1] - lo[1])
    if exact:
        corners = np.vstack([outer, *inner])
        xs, ys = _cuts(corners[:, 0], sx), _cuts(corners[:, 1], sy)
    else:
        n = int(round(2 / FIBRE_SIZE))
        xs, ys = np.linspace(lo[0], hi[0], n + 1), np.linspace(lo[1], hi[1], n + 1)
    cx, cy = np.meshgrid(0.5 * (xs[:-1] + xs[1:]), 0.5 * (ys[:-1] + ys[1:]), indexing="ij")
    wx, wy = np.meshgrid(np.diff(xs), np.diff(ys), indexing="ij")
    centres = np.column_stack([cx.ravel(), cy.ravel()])
    keep = _inside(centres, outer)
    for hole in inner:
        keep &= ~_inside(centres, hole)
    return np.column_stack([centres[keep], (wx * wy).ravel()[keep]])


def _ring_fibres(r_out: float, r_in: float) -> np.ndarray:
    rings = np.linspace(r_in, r_out, TUBE_RINGS + 1)
    dphi = 2 * np.pi / TUBE_SECTORS
    out = []
    for r1, r2 in zip(rings[:-1], rings[1:]):
        area = 0.5 * dphi * (r2**2 - r1**2)
        # the centroid of an annular sector, on its bisector
        rc = (2.0 / 3.0) * (r2**3 - r1**3) / (r2**2 - r1**2) * np.sin(dphi / 2) / (dphi / 2)
        for k in range(TUBE_SECTORS):
            phi = (k + 0.5) * dphi
            out.append((rc * np.cos(phi), rc * np.sin(phi), area))
    return np.asarray(out)


def section_fibres(section) -> np.ndarray:
    """``(n, 3)``: each fibre's local y, local z and area, about the section's centroid."""
    from ada.sections.categories import BaseTypes

    if section.type == BaseTypes.TUBULAR:
        fibres = _ring_fibres(float(section.r), float(section.r) - float(section.wt))
    elif section.type == BaseTypes.CIRCULAR:
        fibres = _ring_fibres(float(section.r), 0.0)
    else:
        profile = section.get_section_profile(True)
        if profile.outer_curve is None:
            raise ValueError(f"section {section.name!r} ({section.type}) has no outline to cut into fibres")
        outer = np.asarray(profile.outer_curve.points2d, dtype=float)[:, :2]
        inner = [] if profile.inner_curve is None else [np.asarray(profile.inner_curve.points2d, dtype=float)[:, :2]]
        exact = _rectilinear(outer) and all(_rectilinear(h) for h in inner)
        fibres = _grid_fibres(outer, inner, exact)
    area = fibres[:, 2].sum()
    centroid = (fibres[:, :2] * fibres[:, 2:3]).sum(axis=0) / area
    fibres[:, :2] -= centroid
    return fibres


def fibre_group(fs: FemSection) -> str:
    """The fibre group of a beam section (24 characters at most, as DEFI_GEOM_FIBRE names them)."""
    return f"fb_{fs.elset.name}"[:24]


def compor_name(index: int) -> str:
    return f"mf_compor{index}"


def geom_fibre_str(beams: list[FemSection]) -> str:
    """``mf_geom = DEFI_GEOM_FIBRE(FIBRE=...)``: one point-fibre group per beam section, and one ``DEFI_COMPOR`` per
    section (von Mises on its fibres, its material's elasticity for the torsion)."""
    from ada.fem.formats.code_aster.write.names import concept_name

    blocks = []
    for fs in beams:
        f = section_fibres(fs.section)
        vals = ",\n            ".join(f"{y!r}, {z!r}, {a!r}" for y, z, a in f.tolist())
        blocks.append(
            f"""    _F(
        GROUP_FIBRE='{fibre_group(fs)}',
        CARA='SURFACE',
        COOR_AXE_POUTRE=(0.0, 0.0),
        VALE=(
            {vals},
        ),
    ),"""
        )
    out = "mf_geom = DEFI_GEOM_FIBRE(\n    FIBRE=(\n" + "\n".join(blocks) + "\n    ),\n)\n"
    for i, fs in enumerate(beams, start=1):
        mat = concept_name(fs.material, "material")
        out += f"""{compor_name(i)} = DEFI_COMPOR(
    GEOM_FIBRE=mf_geom,
    MATER_SECT={mat},
    MULTIFIBRE=_F(GROUP_FIBRE='{fibre_group(fs)}', MATER={mat}, RELATION='VMIS_ISOT_TRAC'),
)
"""
    return out


def cara_elem_str(beams: list[FemSection]) -> str:
    """``AFFE_CARA_ELEM``'s ``GEOM_FIBRE`` and ``MULTIFIBRE``: each beam section's elements their fibre group."""
    rows = "\n".join(
        f"""            _F(GROUP_MA='{fs.elset.name}', GROUP_FIBRE='{fibre_group(fs)}'),""" for fs in beams
    )
    return f"\n        GEOM_FIBRE=mf_geom,\n        MULTIFIBRE=(\n{rows}\n        ),"


def affe_compor_str(beams: list[FemSection]) -> str:
    """``AFFE_MATERIAU``'s ``AFFE_COMPOR``: each fibre beam its section's behaviour."""
    rows = "\n".join(
        f"""        _F(GROUP_MA='{fs.elset.name}', COMPOR={compor_name(i)}),""" for i, fs in enumerate(beams, start=1)
    )
    return f"\n    AFFE_COMPOR=(\n{rows}\n    ),"
