# Eigenvalue analysis detailed results

This section expands per solver. Each `<!-- paradoc:figure ... -->`
block below is handled by the `eig_modes_section` figure-source
registered in `filters.py` — it walks `_assets/<case>/` for baked FEA
bundles tagged with the requested solver and emits a `### case_name`
heading plus `#### Mode N` figures for every poster that baked. Cases
present on disk but not baked (e.g. cache-only CI runs) render with a
"figures unavailable" placeholder; cases for other solvers are skipped
silently.

## Abaqus

Using Abaqus v${ versions.aba } the following results were obtained.

<!-- paradoc:figure
figure_source: eig_modes_section
figure_title: Abaqus eigenvalue results
solver: abaqus
layout: mode_per_section
-->

## Calculix

Using Calculix v${ versions.ccx } the following results were obtained.

<!-- paradoc:figure
figure_source: eig_modes_section
figure_title: Calculix eigenvalue results
solver: calculix
layout: mode_per_section
-->

## Code Aster

Using Code Aster v${ versions.ca } the following results were obtained.

<!-- paradoc:figure
figure_source: eig_modes_section
figure_title: Code Aster eigenvalue results
solver: code_aster
layout: mode_per_section
-->

## Sesam

Using Sesam v${ versions.ses } the following results were obtained.

<!-- paradoc:figure
figure_source: eig_modes_section
figure_title: Sesam eigenvalue results
solver: sesam
layout: mode_per_section
-->
