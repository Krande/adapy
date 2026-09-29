"""``from_gnx`` reads a workspace GeniE has meshed.

Meshing adds a ``mirror_model`` under ``analysis/activities/create_mesh``: GeniE's record of the
concepts and properties the mesh used, as references (``<section section_ref="IB" id="1"/>``,
``<thickness thickness_ref="T10" id="2"/>``). Those elements have no ``name`` and define nothing.
The reader swept ``.//section`` and ``.//thickness``, took them for property definitions, and
failed on every meshed workspace -- ``KeyError: 'name'`` on the section, and once past that an
``AttributeError`` on the thickness, which has no ``constant_thickness`` child.

``meshed_workspace.gnx`` is GeniE V9.2-01's own workspace, saved by ``GenieRuntime.exe <ws> --new
--com=<js> --exit`` after this journal (material, section and thickness set as defaults first)::

    Bm1 = StraightBeam(Point(0 m,0 m,0 m), Point(2 m,0 m,0 m));
    Pl1 = Plate(Point(0 m,0 m,0 m),Point(2 m,0 m,0 m),Point(2 m,1 m,0 m),Point(0 m,1 m,0 m));
    Analysis1 = Analysis(true); Analysis1.add(MeshActivity()); Analysis1.execute(); Save();

with ``IB = ISection(0.3 m, 0.15 m, 0.01 m, 0.02 m)``, ``T10 = Thickness(0.01 m)`` and a 1 m mesh.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile

import pytest

import ada


@pytest.fixture
def meshed_gnx(fem_files):
    return fem_files / "sesam" / "meshed_workspace.gnx"


def test_the_fixture_is_a_meshed_workspace(meshed_gnx):
    """Not vacuous: the workspace carries the nameless mirror-model references."""
    with zipfile.ZipFile(meshed_gnx) as z:
        root = ET.fromstring(z.read("modelData.xml"))
    mirror = root.find("./model/analysis_domain/analyses/analysis/activities/create_mesh/mirror_model")
    assert mirror is not None
    assert [s.attrib for s in mirror.findall("./sections/section")] == [
        {"section_ref": "IB", "id": "1", "modified": "false"}
    ]
    assert [t.attrib["thickness_ref"] for t in mirror.findall("./thicknesses/thickness")] == ["T10"]
    assert all("name" not in e.attrib for e in mirror.iter() if e.tag in ("section", "thickness"))


def test_from_gnx_reads_a_meshed_workspace(meshed_gnx):
    a = ada.from_gnx(meshed_gnx)

    beams = list(a.get_all_physical_objects(by_type=ada.Beam))
    plates = list(a.get_all_physical_objects(by_type=ada.Plate))
    assert [bm.name for bm in beams] == ["Bm1"]
    assert [pl.name for pl in plates] == ["Pl1"]

    # the property table's definitions, and only those
    sections = [s for p in a.get_all_parts_in_assembly(True) for s in p.sections]
    assert [(s.name, s.type) for s in sections] == [("IB", ada.Section.TYPES.IPROFILE)]
    (bm,) = beams
    assert (bm.section.h, bm.section.w_top, bm.section.t_w, bm.section.t_ftop) == (0.3, 0.15, 0.01, 0.02)
    assert plates[0].t == pytest.approx(0.01)
