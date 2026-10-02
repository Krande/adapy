# Detailed results

Mode shapes and deflected shapes from every format that ran on the machine that built this report,
laid out so the formats can be read against each other: one section per mesh configuration, one
heading per mode, and the formats side by side under it. A swapped mode pair or a differently
deflected shape shows up as two pictures that do not match.

Abaqus and Sesam cannot run where this report is built. Their mode shapes are the raw results a
licensed machine committed beside its cached frequencies, and the figures are rendered from them
here; a case with no committed results has no figures. Solver versions: Abaqus v${ versions.aba },
Calculix v${ versions.ccx }, Code Aster v${ versions.ca }, Sesam v${ versions.ses }.

## Cantilever modal participation

Per-mode effective modal mass and participation factors in the global X / Y / Z directions, for
every mesh configuration, with the formats side by side. Code Aster and Sesam report the
translational directions only.

A participation factor Γ is relative to how the solver normalised its eigenvectors: Abaqus scales
the largest displacement to 1, the others scale to unit generalised mass. Raw factors from
different solvers therefore do not compare. The tables give the factor scaled to unit generalised
mass, sign(Γ)·√Meff, which is what the others report as-is. Its sign is the solver's choice of
eigenvector sign and may differ from mode to mode. The effective mass Meff = Γ²·m_gen does not
depend on the normalisation.

### Solid, 1st order

${ eig.meff_solid_o1 }{tbl:sortby:Mode:asc;index:no}

${ eig.pf_solid_o1 }{tbl:sortby:Mode:asc;index:no}

### Solid, 2nd order

${ eig.meff_solid_o2 }{tbl:sortby:Mode:asc;index:no}

${ eig.pf_solid_o2 }{tbl:sortby:Mode:asc;index:no}

### Shell, 1st order

${ eig.meff_shell_o1 }{tbl:sortby:Mode:asc;index:no}

${ eig.pf_shell_o1 }{tbl:sortby:Mode:asc;index:no}

### Shell, 2nd order

${ eig.meff_shell_o2 }{tbl:sortby:Mode:asc;index:no}

${ eig.pf_shell_o2 }{tbl:sortby:Mode:asc;index:no}

### Line, 1st order

${ eig.meff_line_o1 }{tbl:sortby:Mode:asc;index:no}

${ eig.pf_line_o1 }{tbl:sortby:Mode:asc;index:no}

### Line, 2nd order

${ eig.meff_line_o2 }{tbl:sortby:Mode:asc;index:no}

${ eig.pf_line_o2 }{tbl:sortby:Mode:asc;index:no}

## Cantilever eigenvalue analysis

<!-- paradoc:figure
figure_source: fea_modes_compare
figure_title: Cantilever eigenmodes by format
case_prefix: cantilever_EIG
analysis: eigen
beam_solids: true
-->

## Plate eigenvalue analysis

The plain and the stiffened strip, at the eigen case's 0.0625 m seed.

<!-- paradoc:figure
figure_source: fea_modes_compare
figure_title: Plate eigenmodes by format
case_prefix: plate_EIG
analysis: eigen
beam_solids: true
-->

## Plate static analysis

The deflected strip under 1 kPa, plain and stiffened, at the same 0.0625 m seed. The other seeds
deform into the same shape; their convergence is in the summary table.

<!-- paradoc:figure
figure_source: fea_modes_compare
figure_title: Plate deflection by format
case_prefix: plate_static
analysis: static
beam_solids: true
-->
