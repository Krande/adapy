"""Closed-form sway of the portal frame -- the independent third opinion.

Two solvers agreeing proves only that the translation is consistent. It does not prove
either is right: a section modulus wrong by the same factor in both decks, or a load both
writers scale identically, agrees beautifully and is wrong. This module is what makes that
visible.

The frame is symmetric, the loading (``P/2`` at each top corner) is exactly antisymmetric,
so the sway mode has both top joints translating by the same ``delta`` and rotating by the
same ``theta``, and the girder carries no axial force. Two equations close it: joint moment
equilibrium at a top corner, and horizontal equilibrium of the two column shears against
``P``. Slope-deflection gives both.

Euler-Bernoulli (:func:`sway_euler_bernoulli`) reduces to

    delta = P h^3 / (24 E Ic) * (4 + 6 beta) / (1 + 6 beta),   beta = (Ib/L) / (Ic/h)

which is worth sanity-checking at its limits: ``beta -> inf`` (rigid girder) gives
``P h^3 / (24 E Ic)``, i.e. two fixed-fixed columns of stiffness ``12 E I / h^3`` in
parallel; ``beta -> 0`` (no girder, free-to-rotate tops) gives ``P h^3 / (6 E Ic)``, i.e.
two cantilevers each carrying ``P/2``. Both are right.

:func:`sway_timoshenko` is the same derivation with the shear-flexible slope-deflection
coefficients, ``phi = 12 E I / (G As L^2)`` per member. For this frame the two forms differ
by 0.63%, which is exactly the size of gap that would otherwise get mistaken for a
translation defect and "fixed" by loosening a tolerance.

**Neither form alone is the pass criterion**, and that is the important design decision
here. Which one a solver should match depends on its element: Sestra's ``BEAS`` and
Abaqus's ``B31`` are shear-flexible and belong against the Timoshenko value; Abaqus's
``B33`` is a cubic Euler-Bernoulli beam and belongs against the other. Picking one
formulation as *the* reference would make a correct shear-rigid element fail -- which is not
a hypothetical, it is what happened the first time this was wired up. So the criterion is
the **admissible bracket** (:func:`admissible_bracket`): the solver's sway must lie between
the two closed forms, widened by :data:`HAND_CHECK_REL_TOL` at each end, and the report
names which formulation it landed nearest. A value inside the bracket is consistent with
one of the two standard beam theories; a value outside it is wrong regardless of element
choice.

The price of that honesty is resolution: the bracket is about 1.0% wide (0.63% between the
two formulations plus 0.2% of slack at each end), so the hand check cannot police the answer
more tightly than that across two element libraries. It can and does police the things that
actually go wrong, which are all much larger -- a pinned base instead of a fixed one is 4.3x.

What both forms still neglect: axial flexibility of the columns and girder. The columns do
carry axial force (the girder's end shears), which lets the top corners move vertically --
about 15 um here, visible in the solved model -- and feeds back into the sway at the 1e-4
relative level. That is the residual :data:`HAND_CHECK_REL_TOL` has to accommodate, and it
is why it is 2e-3 rather than something tighter.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Tolerance applied to each closed form individually, relative.
#:
#: 2e-3 (0.2%). Budgeted, not tuned: the closed form neglects member axial flexibility,
#: whose effect on this frame shows up at the 1e-4 relative level (the ~15 um vertical
#: movement of the top corners is the visible symptom), and a two-node shear-flexible beam
#: element with exact cubic shape functions -- which BEAS is -- has no discretisation error
#: left to add on a piecewise-linear moment field. 2e-3 is therefore about 5x the known
#: modelling gap: tight enough that any real defect (a wrong second moment of area, a
#: dropped load, a pinned instead of fixed base) blows through it by an order of magnitude
#: or more, loose enough that it is not measuring the axial term.
#:
#: It is applied to *each* formulation to decide which one a solver behaves like, and to
#: widen the admissible bracket between them -- never as a single pass/fail against one of
#: them. See :func:`admissible_bracket` for why.
HAND_CHECK_REL_TOL = 2.0e-3


@dataclass(frozen=True)
class SwayPrediction:
    """One closed-form sway value plus the shear parameters that produced it."""

    #: Horizontal translation of both top corners, metres.
    delta: float
    #: Top-corner rotation about the out-of-plane axis, radians.
    theta: float
    #: ``12 E I / (G As h^2)`` for a column. Zero for the Euler-Bernoulli form.
    phi_column: float
    #: ``12 E I / (G As L^2)`` for the girder. Zero for the Euler-Bernoulli form.
    phi_girder: float
    #: Which formulation produced it.
    formulation: str


def sway_euler_bernoulli(
    *, p_total: float, height: float, span: float, e_mod: float, i_column: float, i_girder: float | None = None
) -> SwayPrediction:
    """Sway with bending flexibility only -- no transverse shear.

    ``i_girder`` defaults to ``i_column`` (this frame uses one section throughout).
    """
    return _sway(
        p_total=p_total,
        height=height,
        span=span,
        e_mod=e_mod,
        i_column=i_column,
        i_girder=i_column if i_girder is None else i_girder,
        phi_column=0.0,
        phi_girder=0.0,
        formulation="euler-bernoulli",
    )


def sway_timoshenko(
    *,
    p_total: float,
    height: float,
    span: float,
    e_mod: float,
    nu: float,
    shear_area: float,
    i_column: float,
    i_girder: float | None = None,
) -> SwayPrediction:
    """Sway including transverse shear flexibility of both columns and girder.

    ``shear_area`` must be the shear area the *solver* uses, not a textbook shear factor.
    For the Sesam side that is ``GBEAMG``'s ``SHARY``, which adapy fills from
    ``Section.properties.Shary`` -- see :func:`model.section_properties`.
    """
    i_girder = i_column if i_girder is None else i_girder
    g_mod = e_mod / (2.0 * (1.0 + nu))
    return _sway(
        p_total=p_total,
        height=height,
        span=span,
        e_mod=e_mod,
        i_column=i_column,
        i_girder=i_girder,
        phi_column=12.0 * e_mod * i_column / (g_mod * shear_area * height**2),
        phi_girder=12.0 * e_mod * i_girder / (g_mod * shear_area * span**2),
        formulation="timoshenko",
    )


def _sway(
    *,
    p_total: float,
    height: float,
    span: float,
    e_mod: float,
    i_column: float,
    i_girder: float,
    phi_column: float,
    phi_girder: float,
    formulation: str,
) -> SwayPrediction:
    """Slope-deflection solution of the antisymmetric sway mode.

    Shear-flexible slope-deflection for a member AB of length ``l`` with shear parameter
    ``phi``, chord rotation ``psi = delta/l``::

        M_AB = EI/(l(1+phi)) * [(4+phi) th_A + (2-phi) th_B - 6 psi]
        M_BA = EI/(l(1+phi)) * [(2-phi) th_A + (4+phi) th_B - 6 psi]

    ``phi = 0`` recovers the Euler-Bernoulli form, which is why one function serves both.

    Column: base A fixed (``th_A = 0``), top B rotates ``theta``, chord rotation
    ``delta/h``. Girder: both ends rotate ``theta`` in the same sense (that is what makes
    the mode antisymmetric), no chord rotation. With ``c = E Ic / (h (1+phi_c))`` and
    ``b = E Ib / (L (1+phi_b))``:

    * joint equilibrium ``M_top + M_girder = 0`` gives
      ``theta = 6 c delta / (h [c (4+phi_c) + 6 b])``;
    * each column's shear is ``(M_top + M_base)/h = (c/h)(6 theta - 12 delta/h)``, and the
      two of them carry ``P``, giving ``P = (2 c / h)(12 delta/h - 6 theta)``.

    Solve the second for ``delta`` with ``theta`` proportional to it.
    """
    c = e_mod * i_column / (height * (1.0 + phi_column))
    b = e_mod * i_girder / (span * (1.0 + phi_girder))

    # theta = k * delta
    k = 6.0 * c / (height * (c * (4.0 + phi_column) + 6.0 * b))
    stiffness = (2.0 * c / height) * (12.0 / height - 6.0 * k)
    delta = p_total / stiffness
    return SwayPrediction(
        delta=delta,
        theta=k * delta,
        phi_column=phi_column,
        phi_girder=phi_girder,
        formulation=formulation,
    )


def admissible_bracket(
    predictions: dict[str, SwayPrediction], *, rel_tol: float = HAND_CHECK_REL_TOL
) -> tuple[float, float]:
    """The band of sway values consistent with *some* standard beam formulation.

    ``[min(delta) * (1 - rel_tol), max(delta) * (1 + rel_tol)]`` over the given predictions.
    A solver inside it is behaving like either a shear-flexible or a shear-rigid beam; one
    outside it has something wrong that is not an element-formulation choice.

    See the module docstring for why this, rather than a single formulation, is the criterion.
    """
    deltas = [p.delta for p in predictions.values()]
    return min(deltas) * (1.0 - rel_tol), max(deltas) * (1.0 + rel_tol)


def portal_frame_predictions() -> dict[str, SwayPrediction]:
    """Both closed forms for the model in :mod:`model`, fed from the model's own section.

    Returns ``{"euler-bernoulli": ..., "timoshenko": ...}``.
    """
    from . import model

    props = model.section_properties()
    common = dict(
        p_total=model.P_TOTAL,
        height=model.HEIGHT,
        span=model.SPAN,
        e_mod=props["E"],
        i_column=props["Iy"],
    )
    return {
        "euler-bernoulli": sway_euler_bernoulli(**common),
        "timoshenko": sway_timoshenko(nu=props["nu"], shear_area=props["shear_area"], **common),
    }
