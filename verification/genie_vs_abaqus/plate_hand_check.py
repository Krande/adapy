"""Closed forms for the plate strip, and the mesh-convergence arithmetic that uses them.

Three closed forms, all exact for the model :mod:`plate_model` builds, plus the two
functions that turn a sequence of solver answers into a converged one.

Why a convergence study replaces the portal frame's bracket
===========================================================

The frame's hand check was a **bracket between two beam theories**: Sestra's ``BEAS`` is the
exact prismatic Timoshenko stiffness and Abaqus' ``B32`` is a quadratic one, so neither has
meaningful discretisation error and the admissible band was set by which *theory* each
element embodies. Shells are the other way round. ``FQUS`` and ``S4R`` are both 4-node
bilinear elements of the same theory, and neither is exact: each converges to the closed form
as the mesh refines, and at any single mesh they differ by *discretisation*, not by
formulation. Measured on the bare strip, mid-span deflection against
``5 q L^4 / (384 D) = 0.173333333333``:

===========  ======  ===============  ==========  ===============  ==========
seed [m]     n span  Sestra ``FQUS``  rel         Abaqus ``S4R``   rel
===========  ======  ===============  ==========  ===============  ==========
0.125        32      0.1731979102     7.813e-04   0.1730654836     1.545e-03
0.0625       64      0.1732994765     1.953e-04   0.1732686013     3.735e-04
0.03125      128     0.1733248681     4.884e-05   0.1733193845     8.047e-05
===========  ======  ===============  ==========  ===============  ==========

Both are **below** the closed form and both quarter their error when the mesh halves. Three
values give one error ratio and it is **4.0000** for Sestra and **3.9997** for Abaqus, i.e.
``p = 2.0000`` and ``p = 1.9999`` -- second order to four figures on both sides, measured
rather than assumed. A single-mesh comparison between them would therefore be reporting the
difference of two discretisation errors: at the coarsest density that difference is 7.65e-04
of the answer, 44x what the *converged* values differ by. That is why the comparison is
between extrapolants.

:func:`observed_order` reads the order off the three values and :func:`richardson`
extrapolates with it. On the bare strip the extrapolants are

    Sestra   0.1733333194  against the closed form 0.1733333333  ->  8.02e-09
    Abaqus   0.1733363138  against the closed form 0.1733333333  ->  1.720e-05

-- i.e. removing the leading discretisation term leaves Sestra on the closed form to eight
figures and Abaqus 1.7e-05 above it, which is the size of its own next-order term. The two
extrapolants agree to 1.720e-05, and that is the number :data:`plate_compare.PLATE_REL_TOL`
is set from.

The stiffened strip, and the one place the closed form is approximate
====================================================================

With the bar the parallel-spring form gives ``0.065200287132`` and the measured sequences are

===========  ===============  ==========  ===============  ==========
seed [m]     Sestra ``FQUS``  rel         Abaqus ``S4R``   rel
===========  ===============  ==========  ===============  ==========
0.125        0.0651635677     5.632e-04   0.0651114956     1.362e-03
0.0625       0.0652009770     1.058e-05   0.0651863739     2.134e-04
0.03125      0.0652091727     1.363e-04   0.0652047023     6.772e-05
===========  ===============  ==========  ===============  ==========

Read the Sestra column carefully: it crosses the closed form between the second and third
mesh and keeps going. The extrapolants are 0.0652114719 (Sestra, order 2.1905) and
0.0652106428 (Abaqus, order 2.0305), so both converge to about **1.65e-04 above** the
parallel-spring value -- 1.715e-04 and 1.588e-04 -- and that is physics rather than a defect.
The closed form treats the stiffened strip
as one beam of ``EI_plate + EI_bar``, which assumes the deflection is uniform across the
width; it is not, because the bar is a line of extra stiffness at ``y = b/2`` while the long
edges are held in cylindrical bending, so the plate spans transversely between them and sags
a little more away from the bar. Measured, at mid-span on the finest mesh:

    Sestra   y = b/2  0.0652091727   y = 0 and y = b  0.0652144477   ->  8.1e-05 apart
    Abaqus   y = b/2  0.0652047023   y = 0 and y = b  0.0652099252   ->  8.0e-05 apart

-- the same transverse variation in both solvers, to 1%, which is what says it is the model
and not one solver's element. On the bare strip the same three points are **identical** to
every digit either solver stores, at every density (that is
:func:`plate_compare.assert_cylindrical`, which reports a spread of exactly 0.0 there). So the
stiffened closed form is good to about 2e-04 here and is used as a *physical* check -- that the
bar carries the load the hand calculation says it carries -- while the cross-solver check is
still between extrapolants at 1e-04. Two questions, two tolerances, in the manner
:mod:`hand_check` already argues for.

The ratio is the sharper statement of the same thing
====================================================

:func:`stiffness_ratio` is ``EI_plate / (EI_plate + EI_bar) = 0.376155502685``, derived from
nothing either solver produced -- two second moments and a modulus. The measured ratio of the
two extrapolated mid-span deflections is **0.376220** (Sestra) and **0.376209** (Abaqus),
1.72e-04 and 1.42e-04 from it, and the stiffened strip is 2.658x stiffer than the bare one.
That is what :func:`plate_compare.assert_stiffener_present` polices, and it is far more
discriminating than any element count: a bar present in the deck but attached to nothing gives
a ratio of 1.000, and a bar rotated 90 degrees about its own axis -- ``I = 3.75e-09`` instead
of ``7.59375e-08``, so ``EI_bar`` falls from 15947 to 788 N m2 -- gives 0.9243. Those are 166%
and 146% away from 0.3762, against a tolerance of 0.1%.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: Tolerance on a single closed form for the **bare** strip, relative.
#:
#: 1e-04, and budgeted from the measurements above rather than tuned. The extrapolated bare
#: deflections miss the closed form by 8.02e-09 (Sestra) and 1.720e-05 (Abaqus), so 1e-04 is
#: 5.8x the worse of the two; the next-order term the extrapolation leaves behind is what it
#: has to accommodate. Every defect it exists for is orders larger: a lost long-edge
#: constraint moves the answer by up to 9% (``1 - nu^2``), a support that fixed a rotation
#: makes it 5x stiffer, and half the load halves it.
BARE_REL_TOL = 1.0e-04

#: Tolerance on the **stiffened** closed form, relative. 1e-03.
#:
#: Ten times looser than :data:`BARE_REL_TOL` for a stated reason, not for comfort: the
#: parallel-spring form neglects the transverse span between the bar and the constrained long
#: edges, measured at 8.0e-05 across the width and leaving the extrapolants 1.715e-04 (Sestra)
#: and 1.588e-04 (Abaqus) above it. 1e-03 is 5.8x that known modelling gap, and it is still
#: 1600x below the thing the stiffened case exists to catch -- a stiffener that carries nothing,
#: which is 2.658x.
STIFFENED_REL_TOL = 1.0e-03

#: Tolerance on the stiffness ratio, relative. 1e-03, for the same reason: the ratio inherits
#: the stiffened case's transverse-span residual, measured at 1.72e-04 (Sestra) and 1.42e-04
#: (Abaqus).
RATIO_REL_TOL = 1.0e-03

#: The mesh refinement factor between consecutive entries of :data:`plate_model.MESH_SIZES`.
#: Two, and the extrapolation arithmetic below is written for it explicitly rather than for a
#: general ratio, because that is what the three seeds are.
REFINEMENT = 2.0

#: An observed convergence order outside this band means the three values are not a single
#: converging sequence, and extrapolating them would invent a number.
#:
#: ``(1.5, 3.0)`` brackets the second order both solvers measure (bare / stiffened: Sestra
#: 2.0000 / 2.1905, Abaqus 1.9999 / 2.0305) with room for the stiffened case's transverse
#: term, and excludes
#: both a first-order sequence -- which is what a locking element or a mesh that is not
#: actually being refined would give -- and a ratio so large it is round-off rather than
#: convergence.
ORDER_BAND = (1.5, 3.0)


class NotConverging(ValueError):
    """Three values that are not a monotone sequence at a single rate.

    Raised rather than extrapolated anyway. Richardson on a sequence that turns around, or
    one whose last two values are equal, produces a number with no meaning -- and it would
    then be compared against another solver's number and reported as agreement or
    disagreement on the strength of nothing.
    """


@dataclass(frozen=True)
class Convergence:
    """One quantity's behaviour over a refinement sequence."""

    #: Coarse to fine.
    values: tuple[float, ...]
    #: The order read off the values, ``log(d1/d2) / log(REFINEMENT)``.
    order: float
    #: The Richardson extrapolant, using :attr:`order`.
    extrapolated: float
    #: ``|value_fine - extrapolated| / |extrapolated|`` -- how much the finest mesh still
    #: has to go.
    remaining: float


def plate_stiffness() -> float:
    """``D = E t^3 / (12 (1 - nu^2))``, the flexural rigidity per unit width, N m.

    ``19230.769230769234`` for this strip. The ``1 - nu^2`` is the whole reason the long edges
    are constrained: it is only right in cylindrical bending. See :mod:`plate_model`.
    """
    from . import plate_model as pm

    props = pm.section_properties()
    return props["E"] * pm.PLATE_THICKNESS**3 / (12.0 * (1.0 - props["nu"] ** 2))


def bare_deflection() -> float:
    """``5 q L^4 / (384 D)`` -- mid-span deflection of the simply supported strip, metres.

    ``0.1733333333333333``. Per unit width on both sides of the fraction, so the strip's
    width cancels and this is the deflection of the whole strip, not of a 1 m slice.
    """
    from . import plate_model as pm

    return 5.0 * pm.PRESSURE * pm.STRIP_LENGTH**4 / (384.0 * plate_stiffness())


def support_rotation() -> float:
    """``q L^3 / (24 D)`` -- the rotation at a simply supported end, radians.

    ``0.1386666666666667``. The second closed form, and the one that says the support is
    *simple*: a support that also fixed the rotation would give zero here and a mid-span
    deflection 5x smaller. Measured against the extrapolated ``ur2`` at ``X0_MID`` on the bare
    strip -- Sestra ``0.1386666745`` (5.6e-08) and Abaqus ``0.1386666480`` (1.3e-07). Neither
    solver was asked for this number; it falls out of the same three solves the deflection came
    from, which is what makes it an independent statement about the support rather than a
    restatement of the deflection.
    """
    from . import plate_model as pm

    return pm.PRESSURE * pm.STRIP_LENGTH**3 / (24.0 * plate_stiffness())


def plate_ei() -> float:
    """``D b`` -- the strip's whole bending stiffness in cylindrical bending, N m2.

    ``9615.384615384617``. ``D`` is per unit width, so this is what adds to the bar's.
    """
    from . import plate_model as pm

    return plate_stiffness() * pm.STRIP_WIDTH


def bar_ei() -> float:
    """``E I`` of the flat bar about the axis the strip bends about, N m2.

    ``15946.874999999998``, from ``Section.properties.Iy`` -- read off the model, and equal to
    ``BAR_WIDTH * BAR_HEIGHT**3 / 12`` to the last bit. The bar's centroid sits on the plate's
    mid-surface in both decks, so there is no ``E A e^2`` term: see :mod:`plate_model`.
    """
    from . import plate_model as pm

    props = pm.section_properties()
    return props["E"] * props["bar_Iy"]


def stiffened_deflection() -> float:
    """``5 (q b) L^4 / (384 (EI_plate + EI_bar))`` -- the parallel-spring estimate, metres.

    ``0.06520028713203371``. ``q b`` because the two stiffnesses are now absolute rather than
    per unit width, so the load has to be per unit *length* of span.

    Good to about 3e-04 on this model, and the residual is physical -- see the module
    docstring. Use :data:`STIFFENED_REL_TOL`, not :data:`BARE_REL_TOL`, against it.
    """
    from . import plate_model as pm

    return 5.0 * (pm.PRESSURE * pm.STRIP_WIDTH) * pm.STRIP_LENGTH**4 / (384.0 * (plate_ei() + bar_ei()))


def stiffness_ratio() -> float:
    """``EI_plate / (EI_plate + EI_bar)`` -- the stiffened deflection as a fraction of the bare.

    ``0.37615550268480996``: the bar makes the strip 2.66x stiffer. Derived from two second
    moments and a modulus, with nothing from either solver in it, which is what makes it the
    check for "did the stiffener arrive and is it attached".
    """
    return plate_ei() / (plate_ei() + bar_ei())


def observed_order(values, *, refinement: float = REFINEMENT) -> float:
    """The convergence order of three values on a uniformly refined sequence, coarse to fine.

    ``p = log((v1 - v0) / (v2 - v1)) / log(refinement)``, the standard three-mesh estimate.
    It is *read* rather than assumed because assuming it is how an extrapolation stops being
    a measurement: a locking element converging at first order, or a "refinement" that did not
    actually change the mesh, both come out of this function as a number outside
    :data:`ORDER_BAND` and are refused by :func:`richardson`.

    Raises :class:`NotConverging` if the two differences have opposite signs (the sequence
    turns around) or the finer difference is zero.
    """
    if len(values) < 3:
        # Two meshes can only confirm a rate that was assumed; they cannot measure one.
        raise NotConverging(
            f"an order needs three values on a uniformly refined sequence; got {len(values)}. "
            f"plate_model.MESH_SIZES has three for exactly this reason."
        )
    v0, v1, v2 = (float(v) for v in values[-3:])
    d1, d2 = v1 - v0, v2 - v1
    if d2 == 0.0:
        raise NotConverging(
            f"the two finest values are identical ({v1!r}), so there is no rate to read and no "
            f"extrapolation to make. Either the mesh did not change or the answer is being "
            f"rounded before it gets here."
        )
    if d1 == 0.0 or (d1 > 0.0) != (d2 > 0.0):
        raise NotConverging(
            f"the sequence {tuple(float(v) for v in values)} does not approach a limit from one "
            f"side: successive differences are {d1!r} and {d2!r}. Richardson extrapolation of a "
            f"sequence that turns around produces a number with no meaning, which would then be "
            f"compared against another solver's and reported as agreement."
        )
    return math.log(abs(d1 / d2)) / math.log(refinement)


def richardson(values, *, order: float | None = None, refinement: float = REFINEMENT) -> Convergence:
    """Richardson-extrapolate three values, coarse to fine, and report what it took.

    ``v_inf = v2 + (v2 - v1) / (refinement**p - 1)``, with ``p`` from
    :func:`observed_order` unless ``order`` is given. Returns a :class:`Convergence` carrying
    the order, the extrapolant and how far the finest mesh still was from it -- all three,
    because an extrapolant on its own hides whether it was an extrapolation or a guess.

    Raises :class:`NotConverging` when the measured order falls outside :data:`ORDER_BAND`.
    """
    sequence = tuple(float(v) for v in values)
    measured = observed_order(sequence, refinement=refinement)
    if order is None:
        low, high = ORDER_BAND
        if not low <= measured <= high:
            raise NotConverging(
                f"the sequence {sequence} converges at order {measured:.4f}, outside the "
                f"admissible band {ORDER_BAND}. Both solvers measure 2.0 on this model, so a rate "
                f"this far off means the three values are not one converging sequence -- an "
                f"element that locks, a mesh that was not refined, or a support that moved between "
                f"runs. Extrapolating it would invent a limit."
            )
        order = measured
    denominator = refinement**order - 1.0
    extrapolated = sequence[-1] + (sequence[-1] - sequence[-2]) / denominator
    remaining = abs(sequence[-1] - extrapolated) / abs(extrapolated) if extrapolated != 0.0 else 0.0
    return Convergence(values=sequence, order=measured, extrapolated=extrapolated, remaining=remaining)


def predictions() -> dict[str, float]:
    """Every closed form for this model, by name. What the report prints beside the solvers."""
    return {
        "D": plate_stiffness(),
        "bare_deflection": bare_deflection(),
        "support_rotation": support_rotation(),
        "EI_plate": plate_ei(),
        "EI_bar": bar_ei(),
        "stiffened_deflection": stiffened_deflection(),
        "stiffness_ratio": stiffness_ratio(),
    }
