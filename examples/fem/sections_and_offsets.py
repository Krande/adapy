"""A small Sesam deck with every beam section type in four end-offset variants.

The point of this model is to be *looked at*. Beam solids are drawn from a section
profile swept along the element axis and then shifted by the element's end
eccentricities, and each of those two steps has a sign and a local axis that is easy
to get subtly wrong -- wrong for one section type, or only when the local z axis is
flipped, or only when the two ends differ. A picture of thirty-two beams that all
ought to line up catches that in a second; a unit test on one beam does not.

So: one row per section type, four beams per row, and the four beams differ only in
how they are offset from their nodes. Run it, open the resulting ``.SIN`` and compare
the rows against this table.

Layout
------
Every beam is 4 m long and runs along +X. Within a row the four variants sit 1.5 m
apart in Y; the rows sit 2 m apart in Z, in the order of ``SECTIONS`` below.

Variants (``e1`` is the offset at the start node, ``e2`` at the end node)::

    a   up = (0, 0,  1)   e1 = none            e2 = none
    b   up = (0, 0,  1)   e1 = (0, 0, -0.3)    e2 = (0, 0, -0.3)
    c   up = (0, 0,  1)   e1 = (0, 0.2, 0.1)   e2 = (0, -0.2, 0.1)
    d   up = (0, 0, -1)   e1 = (0, 0, 0.15)    e2 = (0.3, 0, 0.15)

``a`` is the reference. ``b`` hangs the whole beam below its nodes without tilting
it. ``c`` gives the two ends opposite Y offsets, so the beam axis is rotated away
from the node line -- if a viewer applies one end's offset to both, ``c`` comes out
parallel to ``a`` instead of skewed. ``d`` flips the local z axis *and* adds an axial
(X) component at the far end, which is the case that catches an offset applied in
local instead of global coordinates.

Beams are named ``<SECTION>_<a|b|c|d>``, which is what the deck's TDSCONC concept
names and the SIN's section names carry, so a beam in the picture can be traced back
to its row in the table above.

Analysis
--------
Both ends of every beam are fixed in all six degrees of freedom and a single vertical
point load sits at the midspan node, so each beam is a fixed-fixed beam under a point
load -- enough for Sestra to produce a non-trivial result on every element without
any of them being a mechanism. Each beam is meshed into two elements so that a
midspan node exists.

Note on POLY sections: a ``CurvePoly2d``-based section has no Sesam profile record
(the format has no general-outline beam card that adapy reads or writes) and adapy
does not compute its section properties, so a POLY beam cannot be part of a deck
Sestra can solve. It is deliberately left out of this model rather than written as a
zero-stiffness general beam.

Usage
-----
``python examples/fem/sections_and_offsets.py --out-dir <dir>``

Sestra is located through ``ada.fem.formats.sesam.sesam_exe_locator`` (the DNV
Application Version Manager's default, or ``ADA_SESTRA_EXE``). Pass ``--no-execute``
to write the deck only.
"""

from __future__ import annotations

import argparse
import pathlib

import ada
from ada.config import logger

# One row per section type, in the order they are stacked in Z.
SECTIONS = [
    "HEA300",  # IPROFILE
    "TG650x300x25x40",  # TPROFILE
    "BG800x600x20x30",  # BOX
    "HP180x10",  # ANGULAR
    "UNP180x10",  # CHANNEL
    "FB100x10",  # FLATBAR
    "TUB375x35",  # TUBULAR
    "CIRC100",  # CIRCULAR
]

# variant -> (up, e1, e2). See the table in the module docstring.
VARIANTS = {
    "a": ((0, 0, 1), None, None),
    "b": ((0, 0, 1), (0, 0, -0.3), (0, 0, -0.3)),
    "c": ((0, 0, 1), (0, 0.2, 0.1), (0, -0.2, 0.1)),
    "d": ((0, 0, -1), (0, 0, 0.15), (0.3, 0, 0.15)),
}

LENGTH = 4.0
Y_SPACING = 1.5
Z_SPACING = 2.0
POINT_LOAD = 10e3  # N, downwards at midspan


def beam_name(section: str, variant: str) -> str:
    return f"{section}_{variant}"


def beam_name_at(p) -> str:
    """Name the beam whose node line passes through ``p``.

    Nothing downstream of the deck carries the original beam names onto individual
    elements, so the grid position is what identifies a beam in a deck or a result
    file. Rows are 2 m apart in Z, variants 1.5 m apart in Y.
    """
    row = int(round(float(p[2]) / Z_SPACING))
    col = int(round(float(p[1]) / Y_SPACING))
    return beam_name(SECTIONS[row], list(VARIANTS)[col])


def expected_offsets() -> dict[str, tuple[tuple | None, tuple | None]]:
    """``{beam name: (e1, e2)}`` -- the table in the docstring, as data."""
    return {beam_name(section, variant): (e1, e2) for section in SECTIONS for variant, (_, e1, e2) in VARIANTS.items()}


def build_assembly(mesh_size: float = 2.0) -> ada.Assembly:
    """The model: 32 beams, both ends fixed, a point load at each midspan."""
    beams = []
    for row, section in enumerate(SECTIONS):
        z = row * Z_SPACING
        for col, (variant, (up, e1, e2)) in enumerate(VARIANTS.items()):
            y = col * Y_SPACING
            beams.append(
                ada.Beam(
                    beam_name(section, variant),
                    (0.0, y, z),
                    (LENGTH, y, z),
                    section,
                    up=up,
                    e1=e1,
                    e2=e2,
                )
            )

    part = ada.Part("sections") / beams
    a = ada.Assembly("SectionsAndOffsets") / part

    # mesh_size of half the beam length puts a node at midspan, which is where the
    # load goes. Line elements only; the eccentricities are what is being checked and
    # they live on the beam elements.
    part.fem = part.to_fem_obj(mesh_size, "line")

    end_nodes = []
    mid_nodes = []
    for bm in beams:
        for p, bucket in ((bm.n1.p, end_nodes), (bm.n2.p, end_nodes), ((bm.n1.p + bm.n2.p) / 2, mid_nodes)):
            node = part.fem.nodes.get_by_volume(p=p, single_member=True)
            if node is None:
                raise ValueError(f"No FEM node at {p} for {bm.name}; the mesh size does not put a node there")
            bucket.append(node)

    part.fem.add_bc(ada.fem.Bc("fixed_ends", ada.fem.FemSet("bc_ends", end_nodes), [1, 2, 3, 4, 5, 6]))

    step = a.fem.add_step(ada.fem.StepImplicitStatic("static"))
    for bm, node in zip(beams, mid_nodes):
        step.add_load(
            ada.fem.Load(
                f"load_{bm.name}",
                ada.fem.Load.TYPES.FORCE,
                POINT_LOAD,
                fem_set=ada.fem.FemSet(f"load_{bm.name}", [node]),
                dof=[0, 0, -1, 0, 0, 0],
            )
        )

    return a


def report_sin(sin: pathlib.Path) -> None:
    """Print what the results file holds, and the eccentricity of every element in it.

    This is the check the model exists for: the offsets have to survive the whole way
    round -- concept beam, input deck, Sestra, results file -- and the table below is
    the far end of that trip. The vectors printed are the file's own, which are the
    negation of the ``e1``/``e2`` in the table at the top of this module.
    """
    from ada.fem.formats.sesam.results.read_sin import read_sin_metadata

    md = read_sin_metadata(sin)
    print(f"\n{sin}")
    print(f"  nodes {md.node_count}, elements {md.element_count}")
    print(f"  result cases: {md.result_names}")
    print(f"  fields: {md.fields}")
    for record in ("GECCEN", "GUNIVEC", "GELREF1", "GIORH", "GBOX", "GPIPE", "GLSEC", "GBARM", "GBEAMG"):
        print(f"  {record}: {'present' if record in md.types else 'ABSENT'}")

    res = ada.from_fem_res(sin, fem_format="sesam")
    mesh = res.mesh
    node_p = {int(i): c for i, c in zip(mesh.nodes.identifiers, mesh.nodes.coords)}
    eccentricities = mesh.eccentricities or {}

    rows = []
    for block in mesh.elements:
        for label, refs in zip(block.identifiers, block.node_refs):
            elno = int(label)
            name = beam_name_at(node_p[int(refs[0])])
            vectors = eccentricities.get(elno)
            rows.append((name, elno, vectors))

    print(f"\n  {'beam':<24}{'elem':>6}  eccentricity per node, as the file gives it")
    for name, elno, vectors in sorted(rows):
        shown = "-" if not vectors else [None if v is None else [round(float(c), 6) for c in v] for v in vectors]
        print(f"  {name:<24}{elno:>6}  {shown}")


def main(out_dir: str | pathlib.Path, execute: bool = True, mesh_size: float = 2.0):
    """Build the model, write the Sesam deck into ``out_dir`` and (optionally) run Sestra."""
    out_dir = pathlib.Path(out_dir)
    a = build_assembly(mesh_size=mesh_size)
    res = a.to_fem("sections_and_offsets", "sesam", scratch_dir=out_dir, execute=execute, overwrite=True)
    analysis_dir = out_dir / "sections_and_offsets"
    logger.info(f"Sesam deck written to {analysis_dir}")

    if execute:
        sin = analysis_dir / "sections_and_offsetsR1.SIN"
        if sin.exists():
            report_sin(sin)
        else:
            logger.error(f"Sestra produced no results file at {sin}; see SESTRA.MLG in {analysis_dir}")

    return res


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", default="temp", help="Directory the analysis folder is created in")
    parser.add_argument("--no-execute", action="store_true", help="Write the deck but do not run Sestra")
    parser.add_argument("--mesh-size", type=float, default=2.0, help="Line mesh size (m)")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    main(args.out_dir, execute=not args.no_execute, mesh_size=args.mesh_size)
