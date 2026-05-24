FEA Verification Report
=======================

This interactive report presents the results of eigenvalue analysis verification tests across
multiple FEA software packages supported by ADA.

.. raw:: html

    <p style="margin: 24px 0;">
        <a href="../../_static/fea-report/index.html"
           style="display: inline-block; padding: 12px 20px; background: #2563eb;
                  color: #fff; text-decoration: none; border-radius: 6px;
                  font-weight: 600;">
            Open the interactive FEA verification report →
        </a>
    </p>
    <p style="color: #555; font-size: 0.95em;">
        The report is a standalone paradoc bundle with sortable tables,
        interactive 3D mode-shape viewers, and a frequency-vs-mode plot.
        Use the “← adapy docs” link in the report header to return.
    </p>


About This Report
-----------------

The FEA Verification Report compares eigenvalue analysis results across different:

- **FEA Software**: Code_Aster, CalculiX, Abaqus, Sesam
- **Element Types**: Line (beam), Shell, and Solid elements
- **Element Orders**: 1st and 2nd order elements
- **Mesh Types**: Triangular/Tetrahedral vs Quadrilateral/Hexahedral

Test Geometry
~~~~~~~~~~~~~

The verification tests use a standard IPE400 cantilever beam:

- **Length**: 3.0 m
- **Material**: S420 Carbon Steel
- **Boundary Conditions**: Fixed at one end (cantilever)
- **Analysis Type**: Eigenvalue (natural frequency) analysis

Results Interpretation
~~~~~~~~~~~~~~~~~~~~~~

For each analysis configuration, the report shows:

- Eigenvalue results for multiple vibration modes
- Comparison across different FEA solvers
- Percentage differences from reference values

Generating the Report
~~~~~~~~~~~~~~~~~~~~~

Two pixi tasks drive the report:

.. code-block:: bash

    # Cheap rebuild — consumes whatever's in `_assets/` (committed GLBs,
    # cached eigenvalue JSONs). Run automatically as part of `pixi run docs`.
    pixi run -e docs fea-doc

    # Full regen on a host with FEA solvers installed: re-runs solvers and
    # bakes fresh per-(case, mode) deformed-mesh GLBs into `_assets/`.
    pixi run -e docs fea-doc-regen

The bundle lands at ``docs/_static/fea-report/`` and is served as a
standalone page (linked above). Mode-shape and beam geometry GLBs are
checked into ``verification/_assets/``; the frontend
resolves them by the ``data-3d-key`` attribute on each ``ThreeDView``
substitution.

.. note::

    The verification tests require at least one FEA solver to be installed and configured.
    Supported solvers include: Code_Aster, CalculiX, Abaqus, and Sesam.
    The CI docs build does **not** rerun solvers; it consumes the
    eigenvalue JSON cache and the pre-baked GLBs from the repo.
