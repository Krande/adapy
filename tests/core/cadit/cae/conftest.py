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

    Nothing here lies *on* a plate, and that is the point of the layout rather than an accident: a
    member whose axis lies on a face cannot be expressed in the same CAE part as that face at all
    (the shared edge produces no beam elements -- see
    :data:`ada.cadit.cae.plates.BEAM_ON_PLATE_REFUSAL`), so a model that exercises plates *and*
    beams together has to keep them apart in exactly this way.
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
    )
    return ada.Assembly("CaePlates") / part


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
