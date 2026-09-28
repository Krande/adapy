"""Mode-shape normalization: make the same mode look the same whichever solver computed it.

An eigenvector has no inherent size or sign. Each solver picks its own convention -- Abaqus and
Code_Aster scale a mode so its largest displacement is 1, Calculix and Sestra mass-normalize it --
so one physical mode comes out of four solvers at amplitudes that differ several times over, and
sometimes with the opposite sign. Drawn at a fixed deformation scale, identical modes then look
different, which is exactly what a cross-solver comparison must not do.

Normalizing a mode here means: scale its displacement so the largest translation equals a fixed
fraction of the model's size, and flip it so a deterministic sign convention holds. The factor is
per mode (per field step) and is recorded in the manifest, so the solver's raw values stay
recoverable as ``baked / factor``.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

#: Accepted spellings per axis, lowercased, in x, y, z order. The same list the viewer uses
#: (frontend ``warpComponents.ts``), so the bake, the posters and the viewer agree on which columns
#: are the translation -- Sesam's displacement leads with ``ALL``, a reduction, not an axis.
AXIS_ALIASES: tuple[tuple[str, ...], ...] = (
    ("x", "ux", "dx", "u1", "d1", "tx"),
    ("y", "uy", "dy", "u2", "d2", "ty"),
    ("z", "uz", "dz", "u3", "d3", "tz"),
)

#: Default peak translation of a normalized mode, as a fraction of the model's bounding-box
#: diagonal. Visible without distorting the model, and inside the band the viewer's auto warp
#: scale leaves alone -- so the interactive view and the static posters draw it the same.
DEFAULT_TARGET_FRACTION = 0.1


def translation_columns(components: Sequence[str]) -> list[int]:
    """Columns of (dx, dy, dz) in a displacement record, by component name.

    All three named or none: a field that names only some axes falls back to the positional
    reading (the first three columns), as a field that names none does.
    """
    names = [c.lower() for c in components]
    by_name = [next((i for i, n in enumerate(names) if n in aliases), -1) for aliases in AXIS_ALIASES]
    if all(i >= 0 for i in by_name):
        return by_name
    return list(range(min(3, len(components))))


def reference_length(points: np.ndarray) -> float:
    """The model's bounding-box diagonal -- the length a mode's amplitude is scaled against."""
    pts = np.asarray(points, dtype=float)
    if pts.size == 0:
        return 0.0
    return float(np.linalg.norm(pts.max(axis=0) - pts.min(axis=0)))


def mode_sign(translation: np.ndarray, points: np.ndarray) -> float:
    """+1 or -1: the orientation that makes a mode's sign independent of the solver.

    On the axis carrying most of the motion, the energy-weighted displacement ``sum(u|u|)`` is
    made positive -- a bending mode tips the same way in every solver. A mode antisymmetric on
    that axis (torsion) sums to about zero there; its sign is then taken from how that axis's
    motion correlates with position, on the coordinate it correlates with most.
    """
    if translation.size == 0:
        return 1.0
    axis = int(np.argmax(np.abs(translation).sum(axis=0)))
    u = translation[:, axis]
    weighted = float(np.sum(u * np.abs(u)))
    if abs(weighted) > 1e-3 * float(np.sum(u * u)):
        return 1.0 if weighted > 0 else -1.0
    centred = np.asarray(points, dtype=float) - np.asarray(points, dtype=float).mean(axis=0)
    corr = centred.T @ u  # one correlation per coordinate axis
    k = int(np.argmax(np.abs(corr)))
    return 1.0 if corr[k] >= 0 else -1.0


def mode_scale_factor(
    values: np.ndarray,
    points: np.ndarray,
    columns: Sequence[int],
    ref_length: float,
    target_fraction: float = DEFAULT_TARGET_FRACTION,
) -> float:
    """The signed factor that normalizes one mode: ``values * factor`` is the normalized mode.

    Its largest translation (vector magnitude over ``columns``) becomes
    ``target_fraction * ref_length``, and its sign follows :func:`mode_sign`. Returns 1.0 for a
    mode with no translation or a model with no size -- there is nothing to normalize against.
    """
    translation = np.asarray(values, dtype=float)[:, list(columns)]
    finite = np.isfinite(translation).all(axis=1)
    translation = translation[finite]
    pts = np.asarray(points, dtype=float)[finite]
    peak = float(np.linalg.norm(translation, axis=1).max()) if translation.size else 0.0
    if not (peak > 0.0) or not (ref_length > 0.0):
        return 1.0
    return mode_sign(translation, pts) * target_fraction * ref_length / peak
