"""Pure numerical kernels for DNV Sesam Xtract-compatible result fields.

The functions operate on arrays whose final axis is components. They contain
no reader or viewer state, which makes the calculation order independently
testable against Xtract listings.
"""

from __future__ import annotations

import numpy as np


G_STRESS_COMPONENTS = ("SIGXX", "SIGYY", "TAUXY", "VONMISES")
P_STRESS_COMPONENTS = ("P1", "P2")
D_STRESS_COMPONENTS = ("SIGMX", "SIGMY", "SIGBX", "SIGBY", "TAUMXY", "TAUBXY", "MVONMISES")
R_STRESS_COMPONENTS = ("NXX", "NXY", "NYY", "MXY", "MXX", "MYY")
G_FORCE_COMPONENTS = ("NXX", "NXY", "NXZ", "MXX", "MXY", "MXZ")
B_STRESS_COMPONENTS = ("SIGXX", "SIGBYX", "SIGBZX", "TAUTX", "TAUXY", "TAUXZ", "SIGBYX2", "SIGBZX2")


def plane_von_mises(sig_x, sig_y, tau_xy):
    """Thin-shell/membrane von Mises stress."""

    sig_x = np.asarray(sig_x, dtype=float)
    sig_y = np.asarray(sig_y, dtype=float)
    tau_xy = np.asarray(tau_xy, dtype=float)
    return np.sqrt(sig_x * sig_x + sig_y * sig_y - sig_x * sig_y + 3.0 * tau_xy * tau_xy)


def plane_principal(sig_x, sig_y, tau_xy) -> np.ndarray:
    """Return descending in-plane principal stresses ``[..., (P1, P2)]``."""

    sig_x = np.asarray(sig_x, dtype=float)
    sig_y = np.asarray(sig_y, dtype=float)
    tau_xy = np.asarray(tau_xy, dtype=float)
    centre = 0.5 * (sig_x + sig_y)
    radius = np.sqrt((0.5 * (sig_x - sig_y)) ** 2 + tau_xy * tau_xy)
    return np.stack((centre + radius, centre - radius), axis=-1)


def general_stress(basic: np.ndarray) -> np.ndarray:
    """Append VONMISES to ``[..., (SIGXX, SIGYY, TAUXY)]``."""

    basic = np.asarray(basic, dtype=float)
    if basic.shape[-1] != 3:
        raise ValueError(f"general_stress expects 3 basic components, got {basic.shape}")
    vm = plane_von_mises(basic[..., 0], basic[..., 1], basic[..., 2])
    return np.concatenate((basic, vm[..., None]), axis=-1)


def decompose_shell(bottom: np.ndarray, top: np.ndarray) -> np.ndarray:
    """Compute Xtract D-STRESS from paired lower/upper shell stresses.

    Inputs are ``[..., (SIGXX, SIGYY, TAUXY)]``. Positive bending is
    ``(upper - lower) / 2``, matching Xtract Appendix B and its upper-surface
    sign convention.
    """

    bottom = np.asarray(bottom, dtype=float)
    top = np.asarray(top, dtype=float)
    if bottom.shape != top.shape or bottom.shape[-1] != 3:
        raise ValueError(f"paired shell stresses must have equal [...,3] shapes, got {bottom.shape}/{top.shape}")
    membrane = 0.5 * (top + bottom)
    bending = 0.5 * (top - bottom)
    mv = plane_von_mises(membrane[..., 0], membrane[..., 1], membrane[..., 2])
    return np.stack(
        (
            membrane[..., 0],
            membrane[..., 1],
            bending[..., 0],
            bending[..., 1],
            membrane[..., 2],
            bending[..., 2],
            mv,
        ),
        axis=-1,
    )


def membrane_principal(decomposed: np.ndarray) -> np.ndarray:
    decomposed = np.asarray(decomposed, dtype=float)
    return plane_principal(decomposed[..., 0], decomposed[..., 1], decomposed[..., 4])


def stress_resultants(decomposed: np.ndarray, thickness) -> np.ndarray:
    """Compute Xtract shell resultants from D-STRESS and thickness.

    Output ordering follows the Xtract tree/listing used by the viewer:
    ``NXX, NXY, NYY, MXY, MXX, MYY``.
    """

    d = np.asarray(decomposed, dtype=float)
    t = np.asarray(thickness, dtype=float)
    while t.ndim < d.ndim - 1:
        t = t[..., None]
    t2_over_6 = t * t / 6.0
    return np.stack(
        (
            d[..., 0] * t,
            d[..., 4] * t,
            d[..., 1] * t,
            d[..., 5] * t2_over_6,
            d[..., 2] * t2_over_6,
            d[..., 3] * t2_over_6,
        ),
        axis=-1,
    )


def opposite_section_modulus(inertia: float, primary_modulus: float, full_extent: float) -> float:
    """Recover the opposite-side modulus from I/W and total extent."""

    if inertia <= 0.0 or primary_modulus <= 0.0 or full_extent <= 0.0:
        return np.nan
    primary_distance = inertia / primary_modulus
    opposite_distance = full_extent - primary_distance
    if opposite_distance <= 0.0:
        return np.nan
    return inertia / opposite_distance


def beam_stress(
    forces: np.ndarray,
    *,
    area: float,
    wxmin: float,
    wymin: float,
    wzmin: float,
    shary: float,
    sharz: float,
    wymin2: float | None = None,
    wzmin2: float | None = None,
) -> np.ndarray:
    """Compute Xtract B-STRESS from G-FORCE and section properties."""

    f = np.asarray(forces, dtype=float)
    if f.shape[-1] != 6:
        raise ValueError(f"beam_stress expects [...,6] G-FORCE values, got {f.shape}")
    wy2 = wymin if wymin2 is None else wymin2
    wz2 = wzmin if wzmin2 is None else wzmin2
    denominators = np.asarray((area, wymin, wzmin, wxmin, shary, sharz, wy2, wz2), dtype=float)
    if np.any(denominators <= 0.0) or not np.all(np.isfinite(denominators)):
        return np.full(f.shape[:-1] + (8,), np.nan, dtype=float)
    nxx, nxy, nxz, mxx, mxy, mxz = np.moveaxis(f, -1, 0)
    return np.stack(
        (
            nxx / area,
            -mxy / wymin,
            mxz / wzmin,
            mxx / wxmin,
            nxy / shary,
            nxz / sharz,
            mxy / wy2,
            -mxz / wz2,
        ),
        axis=-1,
    )
