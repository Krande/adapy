## Plate Analysis

The analyses above are one beam, in three representations. This section is the plate case:
an `ada.Plate` object, meshed into shell elements by adapy's own meshing module, written out
as an input deck per solver, and checked against a closed form as well as against the other
solvers.

Nothing here drives a solver through its API. Each solver reads a file adapy wrote — an
`.inp`, a `.fem`, a `.comm`/`.med` — which is the same route a user's own model takes, so what
this section verifies is the thing adapy actually ships.

${ plate.num_cases } cases ran on the machine that built this report, using:
${ plate.solvers }. The remaining columns come from cached results measured elsewhere, the
same arrangement the eigenvalue section uses for Abaqus and Sestra.

### Model Description

A rectangular strip, simply supported on its two short edges and held in cylindrical bending
on the two long ones, under a uniform surface pressure.

| Property          | Value                                    |
|-------------------|------------------------------------------|
| Span              | ${ plate.length_m:.2f } m                |
| Width             | ${ plate.width_m:.2f } m                 |
| Thickness         | ${ plate.thickness_m:.3f } m             |
| Pressure          | ${ plate.pressure_pa:.0f } Pa            |
| Material          | S355 (E = 210 GPa, ν = 0.3, ρ = 7850 kg/m³) |

The span-to-thickness ratio is 400, so transverse shear sits several orders below the
discretisation error and the thin-plate closed forms are the right reference rather than an
approximation of one.

The supports are written as four node sets rather than one per edge. Simple support and
cylindrical bending overlap at the four corners, and the obvious spelling constrains `u2`
twice there: Abaqus and Sestra accept that, while Code_Aster refuses it outright
(`<ASSEMBLA_26> … est bloqué plusieurs fois`). A model that only some solvers will read is not
a cross-solver case, so the corner nodes carry only the rotation the long edges add.

### Static analysis under pressure

The static case is the only one that reaches the distributed-load records — Abaqus'
`*Dsload`, Calculix' `*DLOAD`, Code_Aster's `FORCE_COQUE` and Sesam's `BEUSLO`. The
eigenvalue analyses reach the mass and stiffness matrices and nothing else, so a pressure
written wrongly is invisible to them.

Mid-span deflection against the closed form `5 q L⁴ / (384 D)`, with
`D = E t³ / (12 (1 − ν²))` — which evaluates to ${ plate.closed_form_deflection_m:.6f } m for
this strip. Halving the seed should quarter the remaining error; the three rows are a factor
of two apart so the order is readable off the table rather than asserted.

${ plate.static_plain }{tbl:index:no}

The same strip with a T-profile stiffener along its centreline, meshed as line elements
sharing the shells' nodes. There is no closed form for it — a parallel-spring estimate is not
one — so this is an agreement case between solvers, and it is what carries a T section through
the deck writers.

${ plate.static_stiffened }{tbl:index:no}

#### Sign convention

Every writer above places a positive pressure so that it pushes **into** the face it names.
That is Abaqus' convention, and it has to be shared, because a load direction that depends on
the output format makes a cross-solver comparison meaningless.

It is worth stating which face that is, because it is not a global direction: gmsh winds every
element of this strip with its normal along −z, so the positive face looks *down* and a
positive pressure on it moves the strip *up*. The deflections above are therefore positive.
`tests/fem/test_pressure_load_cross_format.py` pins this per format.

### Eigenvalue analysis

The same plate, as an eigenvalue analysis, against
`f_n = n² π / (2 L²) √(D / (ρ t))` — the simply supported beam frequencies with the beam's
`EI / (ρ A)` replaced by the strip's `D / (ρ t)`. Valid for the cylindrical modes, which are
the ones the long-edge constraint admits.

${ plate.eig_compare }{tbl:sortby:Mode:asc;index:no}

Short description of the solver column names:

* ccx: Calculix
* ca: Code Aster
* ses: Sesam
* aba: Abaqus

A `_st` suffix marks the stiffened variant.
