"""Every FEM writer's behaviour when the model carries a spring.

Springs now arrive in ``fem.elements``, so writers that never saw one before do.
The rule this file pins is one rule for all of them: **a writer either emits the
spring correctly, or leaves it out and says so in the log — it never crashes, and it
never emits the same spring twice.**

Before the change, abaqus, calculix, vtu and ifc each raised on a model with a spring
(ValueError / AttributeError), which cost the caller the whole export.

Abaqus and Sesam read their springs back (`test_abaqus_reads_the_spring_back`,
`test_sesam_reads_the_spring_back`; the zoo's round trips cover the rest). The other formats
cannot be round-tripped, so their assertions check each format's own output instead.
"""

from __future__ import annotations

import logging

import numpy as np
import pytest

import ada
from ada.config import logger
from ada.fem import FemSet, Spring
from ada.fem.steps import StepImplicitStatic

SPRING_ID = 9001


def _model(with_step: bool = False) -> ada.Assembly:
    a = ada.Assembly("A")
    p = ada.Part("P")
    a.add_part(p)
    bm = ada.Beam("bm1", (0, 0, 0), (1, 0, 0), "IPE300")
    p.add_beam(bm)
    p.fem = bm.to_fem_obj(0.5, "line")
    fem_set = FemSet("spr1_set", [p.fem.nodes.from_id(1)], FemSet.TYPES.NSET, parent=p.fem)
    stiff = np.diag([1e5, 2e5, 3e5, 4e5, 5e5, 6e5]).astype(float)
    p.fem.add_spring(Spring("spr1", SPRING_ID, "SPRING1", fem_set=fem_set, stiff=stiff, parent=p.fem))
    if with_step:
        a.fem.add_step(StepImplicitStatic("st1", total_time=1.0, max_incr=1.0, init_incr=1.0))
    return a


@pytest.fixture
def warnings_visible(monkeypatch, caplog):
    """`ada.config` sets propagate = False on the `ada` logger, so caplog's root
    handler never sees a record unless propagation is re-enabled for the test."""
    monkeypatch.setattr(logger, "propagate", True)
    with caplog.at_level(logging.WARNING, logger=logger.name):
        yield caplog


@pytest.mark.parametrize(
    "fem_format, needs_step",
    [("abaqus", False), ("calculix", True), ("code_aster", False), ("sesam", False), ("usfos", False)],
)
def test_a_spring_does_not_abort_the_export(fem_format, needs_step, tmp_path):
    _model(needs_step).to_fem(f"m_{fem_format}", fem_format, scratch_dir=tmp_path, overwrite=True)

    assert list((tmp_path / f"m_{fem_format}").rglob("*")), "writer produced no files"


def test_abaqus_writes_the_spring_once(tmp_path):
    """`springs_str` owns spring output. The element walk newly reaches springs too, so
    without a branch of its own each spring would be defined twice — once by the element
    table and once by `springs_str`."""
    _model().to_fem("ab", "abaqus", scratch_dir=tmp_path, overwrite=True)
    deck = "\n".join(p.read_text() for p in (tmp_path / "ab").rglob("*.inp"))

    # One SPRING1 element per stiffness term, each with its own *Spring; the first keeps the id.
    assert deck.count("*Element, type=SPRING1") == 6
    assert deck.count("*Spring, elset=spr1_") == 6
    assert f"\n{SPRING_ID}, 1\n" in deck
    # The element table's own header style: it must not have written the spring as well.
    assert "*ELEMENT, type=SPRING" not in deck


def test_abaqus_reads_the_spring_back(tmp_path):
    """The deck used to put the element row under *Spring and never define the element, so
    there was nothing to read back; the reader had no *Spring support either."""
    _model().to_fem("ab", "abaqus", scratch_dir=tmp_path, overwrite=True)
    back = ada.from_fem(next((tmp_path / "ab").rglob("ab.inp")), "abaqus")
    (spring,) = [s for p in back.get_all_parts_in_assembly() for s in p.fem.springs.values()]
    assert (spring.name, spring.id, spring.fem_set.name) == ("spr1", SPRING_ID, "spr1_set")
    np.testing.assert_array_equal(spring.stiff, np.diag([1e5, 2e5, 3e5, 4e5, 5e5, 6e5]))


def test_code_aster_writes_the_spring_as_a_point_cell(tmp_path):
    """Code Aster already mapped SpringTypes to MED `PO1` and shared the Mass branch —
    it just never received a spring to write."""
    h5py = pytest.importorskip("h5py")
    _model().to_fem("ca", "code_aster", scratch_dir=tmp_path, overwrite=True)

    med = next((tmp_path / "ca").rglob("*.med"))
    cell_types: set[str] = set()
    with h5py.File(med, "r") as f:
        # MED nests the cell groups under a model- and step-dependent key
        # (ENS_MAA/<model>/<step>/MAI), so find the MAI group rather than spell it.
        f.visititems(lambda name, obj: cell_types.update(obj.keys()) if name.endswith("/MAI") else None)

    assert "PO1" in cell_types, "the spring should be a 1-node point cell"
    assert "SE2" in cell_types, "the beam should still be there"


def test_calculix_reports_the_spring_it_drops(tmp_path, warnings_visible):
    """Calculix has no element row for a spring, and no FemSection to size one from —
    `el_type_sub` dereferenced `fem_sec.parent` and raised."""
    _model(True).to_fem("cx", "calculix", scratch_dir=tmp_path, overwrite=True)

    assert "SpringTypes.SPRING1=1" in warnings_visible.text


def test_sesam_reads_the_spring_back(tmp_path):
    """The writer used to emit no GELMNT1 + MGSPRNG pair, so the spring was lost. It is now a
    GSPR element with its stiffness record and a TDELEM carrying its name and node set."""
    _model().to_fem("se", "sesam", scratch_dir=tmp_path, overwrite=True)
    back = ada.from_fem(next(tmp_path.rglob("seT1.FEM")), "sesam")
    (spring,) = [s for p in back.get_all_parts_in_assembly() for s in p.fem.springs.values()]
    assert (spring.name, spring.id, spring.fem_set.name) == ("spr1", SPRING_ID, "spr1_set")
    np.testing.assert_array_equal(spring.stiff, np.diag([1e5, 2e5, 3e5, 4e5, 5e5, 6e5]))


def test_usfos_reports_the_springs_it_drops(tmp_path, warnings_visible):
    _model().to_fem("uf", "usfos", scratch_dir=tmp_path, overwrite=True)

    assert "skipping 1 spring element(s)" in warnings_visible.text


def test_vtu_skips_the_spring_instead_of_raising(tmp_path, warnings_visible):
    """VTK has no cell for a point element, and raising cost the caller the whole .vtu."""
    from ada.fem.formats.vtu.write import write_to_vtu_file

    mesh = _model().get_by_name("P").fem.to_mesh()
    out = tmp_path / "m.vtu"
    write_to_vtu_file(mesh.nodes, mesh.elements, {}, {}, out)

    assert out.exists() and out.stat().st_size > 0
    assert "SpringTypes.SPRING1=1" in warnings_visible.text


def test_ifc_fem_export_skips_the_spring(tmp_path, warnings_visible):
    """`elem_type_group` maps a spring to LINE, which routed it into `line_elem_to_ifc`
    and straight onto `elem.fem_sec.local_z` — a spring has no FemSection."""
    out = tmp_path / "m.ifc"
    _model().to_ifc(out, include_fem=True, validate=False)

    assert out.exists()
    assert "no IFC FEM representation" in warnings_visible.text
