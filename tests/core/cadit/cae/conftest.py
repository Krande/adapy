"""Fixtures shared by the Abaqus/CAE writer's verification."""

from __future__ import annotations

import pathlib

import pytest

import ada

FILES = pathlib.Path(__file__).resolve().parent / "files"

#: The writer under test. Absent until `src/ada/cadit/cae/` lands.
WRITER_ATTR = "to_abaqus_cae_script"


def writer_available() -> bool:
    return hasattr(ada.Part, WRITER_ATTR)


def require_writer() -> None:
    if not writer_available():
        pytest.skip("Part.{} is not implemented yet (src/ada/cadit/cae/)".format(WRITER_ATTR), allow_module_level=True)


@pytest.fixture(scope="session")
def cae_files() -> pathlib.Path:
    return FILES


@pytest.fixture(scope="session")
def specimen_script() -> pathlib.Path:
    return FILES / "specimen_frame_cae.py"


@pytest.fixture(scope="session")
def specimen_source(specimen_script) -> str:
    return specimen_script.read_text(encoding="utf-8")


@pytest.fixture
def frame_model() -> ada.Assembly:
    """The fixed model behind the golden file and the graph pass.

    A portal frame whose brace lands mid-span of the girder -- the probed case where CAE imprints and
    splits the through member into two sub-edges, so the writer must locate members by bounding
    cylinder. The brace carries an **angular** section, which is the only member here with a non-zero
    product of inertia.
    """
    box = ada.Section("BG200x200x10", "BG", h=0.2, w_top=0.2, w_btn=0.2, t_w=0.01, t_ftop=0.01, t_fbtn=0.01)
    angle = ada.Section("HP200x10", "HP", h=0.2, w_btn=0.2, t_w=0.01, t_fbtn=0.01)
    part = ada.Part("Frame")
    part / (
        ada.Beam("col1", (0, 0, 0), (0, 0, 4), "IPE300", "S355"),
        ada.Beam("girder", (0, 0, 4), (6, 0, 4), box, "S355"),
        ada.Beam("brace", (3, 0, 4), (3, 2, 0), angle, "S355"),
    )
    return ada.Assembly("CaeFrame") / part


@pytest.fixture(scope="session")
def plate_model() -> ada.Assembly:
    """The fixed model behind the plate half of the verification: two plates and two members.

    A deck at ``z = 0`` and a bulkhead at ``y = 0`` meeting along the deck's own boundary edge, so
    the two plates share topology in the ACIS body. A girder 0.5 m below the deck, and a column
    from the girder up to ``(0, 2, 0)`` -- the **midpoint of the deck's boundary edge**, which is
    the case that genuinely connects: measured, CAE splits that edge and the resulting node comes
    out shared by shell and beam elements (``['S4R', 'S4R', 'B31']``).

    A stiffener lying **on** the deck along that same line, which is the case an ordinary edge cannot
    carry: measured, an edge shared with a shell face takes a beam section and produces no elements at
    all, and ``Part.Stringer`` on the same edge produces them with every node shared (see
    :data:`ada.cadit.cae.plates.STRINGER_MEASUREMENT`). So this one model exercises all three kinds of
    member -- a wire clear of every plate, a wire meeting one at a point, and a stringer on one.
    """
    part = ada.Part("PlateFrame")
    part / (
        ada.Plate("deck", [(0, 0), (6, 0), (6, 4), (0, 4)], 0.012, mat="S355"),
        ada.Plate(
            "bulkhead",
            [(0, 0), (6, 0), (6, 3), (0, 3)],
            0.010,
            mat="S355",
            origin=(0, 0, 0),
            xdir=(1, 0, 0),
            normal=(0, -1, 0),
        ),
        ada.Beam("girder", (0, 2, -0.5), (6, 2, -0.5), "IPE300", "S355"),
        ada.Beam("column", (0, 2, -0.5), (0, 2, 0), "IPE300", "S355"),
        # A stiffener lying ON the deck, along the line the column reaches. It becomes a CAE
        # Stringer rather than a wire, and it splits the deck into two faces.
        ada.Beam("stf", (0, 2, 0), (6, 2, 0), "HP200x10", "S355"),
    )
    return ada.Assembly("CaePlates") / part


@pytest.fixture(scope="session")
def region_model() -> ada.Assembly:
    """The strip whose supports are EDGE and FACE regions rather than vertices.

    A 4.0 x 0.5 m plate, 10 mm, simply supported on its two short edges and held in cylindrical
    bending on both long ones -- the case ``ada.cadit.cae.analysis`` used to refuse, because the
    interior nodes of a plate edge are at no vertex. ``SS_X0`` and ``SS_X1`` are one edge each,
    ``CYL`` is two *parallel* edges and therefore two bounding boxes, and ``ALLFIX`` is the plate's
    whole mesh and therefore a face region. One model reaching all three region kinds but the vertex
    one, which every other specimen here already carries.
    """
    from ada.fem import Bc, FemSet, StepImplicitStatic

    length, width, thickness = 4.0, 0.5, 0.010
    part = ada.Part("Strip") / (ada.Plate("strip", [(0, 0), (length, 0), (length, width), (0, width)], thickness),)
    assembly = ada.Assembly("StripSite") / part
    part.fem = part.to_fem_obj(0.125, "line", use_quads=True, interactive=False)
    fem = part.fem
    tol = 1e-09
    groups = {
        "ALLFIX": (list(fem.nodes), [1, 2, 3, 4, 5, 6]),
        "CYL": ([n for n in fem.nodes if abs(n.y) < tol or abs(n.y - width) < tol], [2, 4]),
        "SS_X0": ([n for n in fem.nodes if abs(n.x) < tol], [1, 2, 3]),
        "SS_X1": ([n for n in fem.nodes if abs(n.x - length) < tol], [2, 3]),
    }
    for name in sorted(groups):
        nodes, dofs = groups[name]
        fem_set = fem.add_set(FemSet(name, sorted(nodes, key=lambda n: n.id), FemSet.TYPES.NSET, parent=fem))
        fem.add_bc(Bc(name, fem_set, dofs))
    assembly.fem.add_step(StepImplicitStatic("static", nl_geom=False, total_time=1, init_incr=1, max_incr=1))
    return assembly


@pytest.fixture(scope="session")
def region_specimen_source(region_model, tmp_path_factory) -> str:
    """The writer's own output for :func:`region_model`, for the region checks to be shown teeth on.

    Generated rather than committed, for the reason :func:`plate_specimen_source` gives: the bounding
    boxes are computed from the ACIS body adapy authors, so a hand-written specimen would have to
    carry a hand-written body beside it. The independent oracle for the *numbers* is the licensed
    run, which builds these very boxes in the kernel and checks what each of them returns.
    """
    workdir = tmp_path_factory.mktemp("cae_region_specimen")
    written = region_model.to_abaqus_cae_script(workdir / "specimen_regions.py", mesh_size=0.125)
    return written[0].read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def plate_specimen_source(plate_model, tmp_path_factory) -> str:
    """The writer's own output for :func:`plate_model`, for the graph checks to be shown teeth on.

    Generated rather than committed, unlike ``specimen_frame_cae.py``: a hand-written plate specimen
    would have to carry a hand-written ACIS body beside it, and the thing the plate checks read is
    the ``PLATES`` table the writer computes from that body. The independent oracles for the
    *content* are the golden file and the licensed run; what this specimen is for is showing that
    each plate check rejects a defect.
    """
    workdir = tmp_path_factory.mktemp("cae_plate_specimen")
    written = plate_model.get_part("PlateFrame").to_abaqus_cae_script(workdir / "specimen_plates.py")
    return written[0].read_text(encoding="utf-8")
