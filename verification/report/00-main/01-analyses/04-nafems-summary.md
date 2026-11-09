## NAFEMS Benchmarks

The cantilever and the plate are checked against formulas this report derives. These two cases are
checked against values published independently, in The Standard NAFEMS Benchmarks (TNSB Rev. 3).

${ nafems.num_cases } cases ran on the machine that built this report, using: ${ nafems.solvers }.
Other columns come from cached results measured elsewhere, the same arrangement the eigenvalue
section uses for Abaqus and Sestra.

### Model Description

Both benchmarks are a quarter of the region between two confocal ellipses, with point D on the inner
ellipse at x = 2 m, y = 0.

| Property | Value |
|----------|-------|
| Inner ellipse | (x/2)² + y² = 1 |
| Outer ellipse | (x/3.25)² + (y/2.75)² = 1 |
| Material | E = 210 GPa, ν = 0.3 |
| Elements | 20-node hexahedra |

The mesh is written directly rather than generated. Confocal ellipses map exactly onto a rectangle in
elliptic coordinates, so every node sits on the true geometry and the elements are orthogonal. The
error in the tables is then the element's and the solver's, not a mesher's. Each row doubles the
divisions of the one before it.

The restraints are collected per node and written once for each distinct set of degrees of freedom.
One set per face would hold a degree of freedom twice along the edges where faces meet, which
Code_Aster refuses.

### LE1: elliptic membrane

Plane stress, under a uniform outward pressure of 10 MPa on the outer edge, with symmetry on x = 0
and y = 0. The target is the tangential stress at D, σyy = 92.7 MPa.

${ nafems.le1_geometry }

The benchmark is defined in plane stress, and adapy's solid elements are used for it here: one layer
over half the 0.1 m thickness, with a symmetry plane at mid-thickness and free faces. That leaves no
through-thickness stress, so the solution is the plane-stress one.

${ nafems.le1 }{tbl:index:no}

σyy over the plan at the finest mesh, the quantity the benchmark is judged on. All solvers share one
colour scale, and D is marked with each solver's own value.

${ nafems.le1_contour }

Displacement at the finest mesh, deformed, from each solver. The interactive view shows the same
result in 3D.

<!-- paradoc:figure
figure_source: fea_modes_compare
figure_title: NAFEMS LE1 results by solver
case_prefix: nafems_LE1
analysis: static
heading_level: 4
-->

### LE10: thick plate under pressure

A plate 0.6 m thick, under 1 MPa on its upper face. Symmetry on x = 0 and y = 0, the outer face held
in x and y, and the mid-plane of the outer face also held in z. The target is σyy on the upper face
at D, -5.38 MPa.

${ nafems.le10_geometry }

${ nafems.le10 }{tbl:index:no}

σyy on the upper face at the finest mesh, the quantity the benchmark is judged on. All solvers share
one colour scale, and D is marked with each solver's own value.

${ nafems.le10_contour }

Displacement at the finest mesh, deformed, from each solver. The interactive view shows the same
result in 3D.

<!-- paradoc:figure
figure_source: fea_modes_compare
figure_title: NAFEMS LE10 results by solver
case_prefix: nafems_LE10
analysis: static
heading_level: 4
-->
