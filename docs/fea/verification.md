# FEA Verification Report

This interactive report presents the results of eigenvalue analysis verification tests across
multiple FEA software packages supported by ADA.

[Open the interactive FEA verification report :material-arrow-right:](../_static/fea-report/index.html){ .md-button .md-button--primary }

Download the report:
[PDF](../_static/fea-report-files/fea-report.pdf){ download } ·
[Word (DOCX)](../_static/fea-report-files/fea-report.docx){ download } ·
[OpenDocument (ODT)](../_static/fea-report-files/fea-report.odt){ download }

The report is a standalone paradoc bundle with sortable tables,
interactive 3D mode-shape viewers, and a frequency-vs-mode plot.
Use the “← adapy docs” link in the report header to return.

## About This Report

The FEA Verification Report compares eigenvalue analysis results across different:

- **FEA Software**: Code_Aster, CalculiX, Abaqus, Sesam
- **Element Types**: Line (beam), Shell, and Solid elements
- **Element Orders**: 1st and 2nd order elements
- **Mesh Types**: Triangular/Tetrahedral vs Quadrilateral/Hexahedral

### Test Geometry

The verification tests use a standard IPE400 cantilever beam:

- **Length**: 3.0 m
- **Material**: S420 Carbon Steel
- **Boundary Conditions**: Fixed at one end (cantilever)
- **Analysis Type**: Eigenvalue (natural frequency) analysis

### Results Interpretation

For each analysis configuration, the report shows:

- Eigenvalue results for multiple vibration modes
- Comparison across different FEA solvers
- Percentage differences from reference values

### Generating the Report

Two pixi tasks drive the report:

```bash
# Full build: runs every installed solver and bakes fresh mode-shape
# bundles into `_assets/`. Run automatically as part of `pixi run docs`.
pixi run -e docs fea-doc

# Rebuild reusing paradoc's task cache and whatever `_assets/` holds.
pixi run -e docs fea-doc-cached
```

The bundle lands at `docs/_static/fea-report/` and is served as a
standalone page (linked above). `verification/_assets/` is a build
directory and is not committed; the frontend resolves its GLBs by the
`data-3d-key` attribute on each `ThreeDView` substitution.

!!! note
    Code_Aster and CalculiX are installed in the `docs` environment and
    run on every build, CI included. Abaqus and Sesam need a licence, so a
    licensed machine commits what they measured and every other build
    replays it:

    - `verification/.cache/<case>.json` (and `.cache-plate/` for the
      plate strip) holds the frequencies, eigenvalues, participation factors
      and effective masses;
    - `verification/.cache/<case>/` beside it holds the raw mode shapes as
      an FEA artefact bundle (`fea.mesh.glb`, the nodal displacement blob
      and `fea.manifest.json`). A build copies it into `_assets/` and
      renders the per-mode posters from it, so the PNGs are never committed.

    A licensed run writes both whenever a JSON snapshot is saved (unset
    `ADA_FEM_DO_NOT_SAVE_CACHE`). Review the size of the bundles before
    committing them: a lean bundle is about 0.4–1.5 MB per case.
