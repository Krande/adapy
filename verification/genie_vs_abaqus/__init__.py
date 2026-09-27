"""Cross-solver validation: one adapy concept model, Sestra and Abaqus, compared.

The point of this package is to test a *translation*, not two hand-built models. So
:mod:`model` builds the concept once in adapy and every solver input is derived from
that one object. :mod:`compare` states in detail what such a comparison can and cannot
prove -- read that docstring before trusting a green result.

Two cases live here, asking different questions of the same translation. The **portal frame**
compares two beam formulations at one mesh, node for node, against a bracket between two beam
theories -- neither element has meaningful discretisation error, so the residual is formulation.
The **plate strip** compares two shell formulations that both converge with mesh, so it is a
*mesh-convergence bracket*: three densities per solver per variant, each shown converging toward
the closed form, and the comparison taken between the Richardson extrapolants. Read
:mod:`plate_compare`'s docstring for why that is not the same test with a different model.

Layout::

    model.py                 the concept model (portal frame), its probes, its constants
    hand_check.py            closed-form portal sway, Euler-Bernoulli and Timoshenko
    displacements.py         the exchange format + coordinate-based node matching (both cases)
    sestra_runner.py         adapy -> Sesam FEM -> Sestra.exe -> .SIN -> DisplacementTable
    abaqus_runner.py         adapy -> CAE script -> Abaqus/Standard -> DisplacementTable
    compare.py               tolerances, per-point differences, the loud-failure rules

    plate_model.py           the plate concept model: a strip, bare and stiffened, three seeds
    plate_hand_check.py      the three closed forms, the observed order, Richardson
    plate_sestra_runner.py   shells as FQUS, and the nodal load the Sesam writer needs instead
    plate_abaqus_runner.py   shells as S4R, supports on plate edges, the writer's own solve
    plate_compare.py         the convergence report and the plate case's own loud failures

    run_comparison.py        the CLI for both (``--case frame`` / ``--case plate``)
    selftest.py              every guard in both cases, run against broken inputs

These are scripts, not pytest tests: they need licensed third-party solvers
(Sestra, Abaqus) that no CI runner has. :mod:`selftest` is the exception -- it needs neither, and
``tests/core/test_genie_vs_abaqus_selftest.py`` runs it in the ordinary suite.
"""
