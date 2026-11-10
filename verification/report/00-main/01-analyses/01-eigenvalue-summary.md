## Eigenvalue Analysis

A simple cantilevered beam is subjected to eigenvalue analysis with a total of
${ eig.num_modes } eigenmodes requested. ${ eig.num_cases } cases are compared
across solvers: ${ eig.solvers }.

The beam model will be analyzed by varying the following parameters:

- Mesh type (Solid, Shell, Line)
- Mesh element types (TET, HEX, TRI, QUAD)
- Mesh element order (1st and 2nd order)
- Reduced integration elements (R)
- Different FEA solvers (Calculix, Code Aster, Abaqus, Sesam)

### Model Description

| Property         | Value                                  |
|------------------|----------------------------------------|
| Description      | ${ beam.description }                  |
| Length           | ${ beam.length_m:.2f } m               |
| Section          | ${ beam.section_name }                 |
| Material         | ${ beam.material_name }                |
| Young's modulus  | ${ beam.youngs_modulus_pa:.3e } Pa     |
| Yield stress     | ${ beam.yield_stress_pa:.3e } Pa       |
| Density          | ${ beam.density_kgm3:.0f } kg/m³       |

${ beam.geometry_3d }

### Summary

This section presents a comparison between the calculated results from the
eigenvalue analysis of a cantilever beam using the different FEA tools.

Note! The `Sestra` and `Abaqus` analyses are not performed as part of the github actions step. The
eigenmode results are kept in cached json files and imported during the compilation of the report using __paradoc__.

#### Eigenfrequency vs. mode number

${ eig.freq_vs_mode_plot }

#### Comparison tables

Columns are sortable; the table grammar `{tbl:sortby:Mode:asc;index:no}` sets the
initial sort and hides the index column.

${ eig.compare_solid_o1 }{tbl:sortby:Mode:asc;index:no}

${ eig.compare_solid_o2 }{tbl:sortby:Mode:asc;index:no}


${ eig.compare_shell_o1 }{tbl:sortby:Mode:asc;index:no}

${ eig.compare_shell_o2 }{tbl:sortby:Mode:asc;index:no}


${ eig.compare_line_o1 }{tbl:sortby:Mode:asc;index:no}


${ eig.compare_line_o2 }{tbl:sortby:Mode:asc;index:no}


#### Bending modes against Euler-Bernoulli

The tables above compare the solvers with each other, which shows agreement but not accuracy. The
bending modes of a clamped-free beam have a closed form, `f_n = (β_n L)² / (2π L²) √(E I / (ρ A))`.
Modes are matched by the direction they move in rather than by number, because torsion modes fall
between the bending modes at different places in each representation. Y modes bend about the weak
axis and Z modes about the strong axis. Only the first two in each direction are listed: past those,
the shell and solid models have local modes of the web that also move mainly in Y.

Euler-Bernoulli ignores shear deformation and rotary inertia, and this beam is short: its length is
7.5 times its depth. A correct model that includes those effects lands below the closed form, by
more in the higher modes. So the numbers to read are the trend and how closely the representations
agree, and a large negative deviation here is the theory's limit rather than a solver's error.

${ eig.bending_line }{tbl:index:no}

${ eig.bending_shell }{tbl:index:no}

${ eig.bending_solid }{tbl:index:no}

#### Effective modal mass

Effective modal mass [kg] per case, summed over the captured modes in the
global X / Y / Z directions. Every solver reports it; Code Aster and Sesam
give translational mass only. Summing over enough modes approaches the
structure's total mass in each direction.

${ eig.eff_mass_summary }{tbl:sortby:Case:asc;index:no}

The per-mode effective masses and participation factors, compared across
solvers for every mesh configuration, are in the appendix.



Short description:

**Fem formats**

* ccx: Calculix
* ca: Code Aster
* ses: Sesam
* aba: Abaqus

**Element Types**

* TET: Tetrahedrons
* HEX: Hexahedrons
* TRI: Triangle
* QUAD: Quadrilateral

**Reduced Integration**

All element types ending with capital R (QUAD**R**, HEX**R** etc.) are reduced integration elements.
