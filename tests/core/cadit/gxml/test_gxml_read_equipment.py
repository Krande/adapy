"""GeniE equipment read from a GeniE export: the prism, its placement, and the beam load GeniE
resolved it into (``genie_loads_segments.xml``: Eq1, 1000 kg, placed at (2, 10, 0) in LC4 on Bm5,
which runs 0..4 along x at y=10)."""

from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET

import numpy as np
import pytest

import ada
from ada.cadit.gxml.read.read_equipment import _axes
from ada.cadit.gxml.read.read_loads import _Refused
from ada.fem.concept.loads import LoadConceptLine
from ada.fem.formats import conversion_report

STAGE = "genie xml reader"


def _read(example_files, tmp_path, fixture):
    # Read a copy: the reader writes a .sat beside the XML it is given.
    src = example_files / "fem_files" / "sesam" / fixture
    dst = tmp_path / fixture
    shutil.copy(src, dst)
    with conversion_report.collect() as report:
        a = ada.from_genie_xml(dst)
    (part,) = a.get_all_subparts()
    return part, report


def test_a_placed_prism_is_read_with_the_beam_load_genie_resolved_it_into(example_files, tmp_path):
    part, report = _read(example_files, tmp_path, "genie_loads_segments.xml")
    eq = part.concept_fem.equipment

    prism = eq.prisms["Eq1"]
    assert (prism.mass, prism.dimensions, prism.cog) == (1000.0, (1.0, 1.0, 1.0), (0.0, 0.0, 0.5))
    assert prism.local_box() == ((-0.5, -0.5, 0.0), (0.5, 0.5, 1.0))

    (placed,) = eq.placements
    assert (placed.equipment, placed.load_case, placed.origin) == ("Eq1", "LC4", (2.0, 10.0, 0.0))
    assert placed.to_global(prism.cog) == (2.0, 10.0, 0.5)

    # Bm5 0.375..0.625 of 0..4 is x=1.5..2.5 under the footprint, at 9806.65 N/m: GeniE carried
    # the 1000 kg at g as 9806.65 N over one metre.
    ((name, load),) = eq.generated_loads["LC4"]
    assert name == "Eq1" and isinstance(load, LoadConceptLine)
    assert tuple(load.start_point) == (1.5, 10.0, 0.0) and tuple(load.end_point) == (2.5, 10.0, 0.0)
    length = float(np.linalg.norm(np.asarray(load.end_point) - np.asarray(load.start_point)))
    assert length * load.intensity_start[2] == pytest.approx(-prism.mass * 9.80665)

    # resolved loads are not applied loads, and the meshed copies of PL1 / LL1 are not equipment
    assert part.concept_fem.loads.load_cases["LC4"].loads == []
    assert set(eq.generated_loads) == {"LC4"}
    assert not [f for f in report.findings if f.stage == STAGE and "equip" in f.keyword]


def test_a_placement_keeps_its_frame_and_refuses_one_that_is_not():
    # GeniE's wind "equipment" stands on its side: x along x, y up, z towards -y.
    frame = ET.fromstring(
        '<local_system><vector x="1" y="0" z="0" dir="x"/><vector x="0" y="0" z="1" dir="y"/>'
        '<vector x="0" y="-1" z="0" dir="z"/></local_system>'
    )
    assert _axes(frame) == ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0))
    mirrored = ET.fromstring(
        '<local_system><vector x="1" y="0" z="0" dir="x"/><vector x="0" y="1" z="0" dir="y"/>'
        '<vector x="0" y="0" z="-1" dir="z"/></local_system>'
    )
    with pytest.raises(_Refused):
        _axes(mirrored)
