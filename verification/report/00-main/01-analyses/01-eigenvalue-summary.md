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


#### Effective modal mass

Effective modal mass [kg] per case, summed over the captured modes in the
global X / Y / Z directions (Calculix and Code Aster report it; Code Aster
gives translational mass only). Summing over enough modes approaches the
structure's total mass in each direction.

${ eig.eff_mass_summary }{tbl:sortby:Case:asc;index:no}



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
