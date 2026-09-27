# Detailed results

Mode shapes and deflected shapes from every format that ran on the machine that built this report,
laid out so the formats can be read against each other: one section per mesh configuration, one
heading per mode, and the formats side by side under it. A swapped mode pair or a differently
deflected shape shows up as two pictures that do not match.

Only formats that ran on this machine have figures. Abaqus and Sesam results come from cached
frequencies (see the summary tables), not from result files, so they carry no mode shapes here.
Solver versions: Abaqus v${ versions.aba }, Calculix v${ versions.ccx }, Code Aster v${ versions.ca },
Sesam v${ versions.ses }.

## Cantilever eigenvalue analysis

<!-- paradoc:figure
figure_source: fea_modes_compare
figure_title: Cantilever eigenmodes by format
case_prefix: cantilever_EIG
analysis: eigen
-->

## Plate eigenvalue analysis

The plain and the stiffened strip, at the eigen case's 0.0625 m seed.

<!-- paradoc:figure
figure_source: fea_modes_compare
figure_title: Plate eigenmodes by format
case_prefix: plate_EIG
analysis: eigen
-->

## Plate static analysis

The deflected strip under 1 kPa, plain and stiffened, at the same 0.0625 m seed. The other seeds
deform into the same shape; their convergence is in the summary table.

<!-- paradoc:figure
figure_source: fea_modes_compare
figure_title: Plate deflection by format
case_prefix: plate_static
analysis: static
-->
