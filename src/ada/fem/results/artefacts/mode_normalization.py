"""Mode-shape normalization: draw every solver's modes at the same deformation scale.

An eigenvector has no inherent size. Each solver picks its own convention -- Abaqus and
Code_Aster scale a mode so its largest displacement is 1, Calculix and Sestra mass-normalize it --
so one physical mode comes out of four solvers at amplitudes that differ several times over.
Drawn at a fixed deformation scale, identical modes then look different.

Normalizing a mode here means only this: a positive factor scales its displacement so the largest
translation equals a fixed fraction of the model's size. The solver's shape and sign are kept.
The factor is per mode (per field step) and is recorded in the manifest, so the solver's raw
values stay recoverable as ``baked / factor``.
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


#: Accepted spellings per rotation axis, lowercased, x, y, z order -- Sesam RX.., Code_Aster DRX..,
#: Abaqus UR1... The same list as the viewer's ``warpComponents.ts``.
ROTATION_ALIASES: tuple[tuple[str, ...], ...] = (
    ("rx", "drx", "ur1", "r1", "rotx", "thx"),
    ("ry", "dry", "ur2", "r2", "roty", "thy"),
    ("rz", "drz", "ur3", "r3", "rotz", "thz"),
)


def rotation_columns(components: Sequence[str]) -> list[int] | None:
    """Columns of (rx, ry, rz) in a displacement record, by component name; None unless all three
    are named. No positional fallback: a three-column record has no rotations, and guessing slots
    3..5 of an unnamed one would twist a model by whatever sits there."""
    names = [c.lower() for c in components]
    by_name = [next((i for i, n in enumerate(names) if n in aliases), -1) for aliases in ROTATION_ALIASES]
    return by_name if all(i >= 0 for i in by_name) else None


def reference_length(points: np.ndarray) -> float:
    """The model's bounding-box diagonal -- the length a mode's amplitude is scaled against."""
    pts = np.asarray(points, dtype=float)
    if pts.size == 0:
        return 0.0
    return float(np.linalg.norm(pts.max(axis=0) - pts.min(axis=0)))


#: A mode whose peak translation is below this fraction of the largest peak among the same
#: result's modes carries no translation -- what is there is round-off. Beam-element torsion
#: modes are the case: the axis nodes only rotate, and their "translation" is 1e-16..1e-10
#: against ~1 for the bending modes beside them. Real small motion (a shell flange in torsion)
#: sits orders of magnitude above this.
TRANSLATION_NOISE_FLOOR = 1e-6


def peak_translation(values: np.ndarray, columns: Sequence[int]) -> float:
    """The largest translation magnitude in one mode, over ``columns``; 0.0 if there is none."""
    translation = np.asarray(values, dtype=float)[:, list(columns)]
    translation = translation[np.isfinite(translation).all(axis=1)]
    return float(np.linalg.norm(translation, axis=1).max()) if translation.size else 0.0


def is_translation_free(peak: float, reference_peak: float | None) -> bool:
    """Whether a mode's translation is only round-off next to its sibling modes'."""
    return bool(reference_peak) and peak < TRANSLATION_NOISE_FLOOR * reference_peak


def mode_scale_factor(
    values: np.ndarray,
    points: np.ndarray,
    columns: Sequence[int],
    ref_length: float,
    target_fraction: float = DEFAULT_TARGET_FRACTION,
    reference_peak: float | None = None,
) -> float:
    """The positive factor that normalizes one mode: ``values * factor`` is the normalized mode.

    Its largest translation (vector magnitude over ``columns``) becomes
    ``target_fraction * ref_length`` -- the deformation scale is aligned across solvers, and
    nothing else: the solver's sign and shape are kept as they are. Returns 1.0 for a mode with
    no translation or a model with no size -- there is nothing to normalize against.

    ``reference_peak`` is the largest peak translation among the result's modes. A mode that is
    translation-free next to it (:func:`is_translation_free`) is scaled like that largest mode
    instead of by its own peak: normalizing round-off to a tenth of the model drew a torsion
    mode of a beam model as a jagged zigzag, where undeformed is the honest picture.
    """
    peak = peak_translation(values, columns)
    if not (ref_length > 0.0):
        return 1.0
    if is_translation_free(peak, reference_peak):
        return target_fraction * ref_length / reference_peak
    if not (peak > 0.0):
        return 1.0
    return target_fraction * ref_length / peak
