"""Cross-solver validation: one adapy concept model, Sestra and Abaqus, compared.

The point of this package is to test a *translation*, not two hand-built models. So
:mod:`model` builds the concept once in adapy and every solver input is derived from
that one object. :mod:`compare` states in detail what such a comparison can and cannot
prove -- read that docstring before trusting a green result.

Layout::

    model.py          the concept model (portal frame), its probe points, its constants
    hand_check.py     closed-form portal sway, Euler-Bernoulli and Timoshenko
    displacements.py  the solver-neutral exchange format + coordinate-based node matching
    sestra_runner.py  adapy -> Sesam FEM -> Sestra.exe -> .SIN -> DisplacementTable
    abaqus_runner.py  the plug point for the Abaqus half (see its docstring)
    compare.py        tolerances, per-point differences, and the loud-failure rules
    run_comparison.py the CLI

These are scripts, not pytest tests: they need licensed third-party solvers
(Sestra, Abaqus) that no CI runner has.
"""
