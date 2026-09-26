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
