"""Every enum the GeniE XML writer serialises is written as its value, never as its ``str()``.

``DesignCondition`` and ``MergeStrategy`` are ``str`` enums, and ``str()`` of one is its qualified
name: the writer put ``design_condition="DesignCondition.OPERATING"`` on every load case and
combination, where GeniE writes ``"operating"``, and ``_analytic_merge_strategy`` lowered
``"MergeStrategy.SURFACE"`` into a token that matched nothing, so an enum argument quietly wrote
polygons instead of the curved shells it asked for.

The sweep below does not list the attributes it expects to be enums: it checks every attribute
of a model that exercises loads, combinations, constraints, hinges, masses and flush beams
against the qualified name of every enum class ``ada`` defines, so a new enum-valued attribute
is caught without this file having to know about it.
"""

from __future__ import annotations

import enum
import re
import xml.etree.ElementTree as ET

import pytest

import ada
from ada.api.beams.base_bm import BeamHinge, BeamHingeDofType
from ada.fem.concept.constraints import (
    ConstraintConceptCurve,
    ConstraintConceptDofType,
    ConstraintConceptPoint,
)
from ada.fem.concept.loads import (
    DesignCondition,
    LoadConceptAccelerationField,
    LoadConceptCase,
    LoadConceptCaseCombination,
    LoadConceptCaseFactored,
    LoadConceptLine,
    LoadConceptPoint,
    LoadConceptSurface,
)


def _ada_enum_names() -> set[str]:
    """The class name of every ``enum.Enum`` subclass defined in an ``ada`` module loaded so far."""
    names, todo = set(), list(enum.Enum.__subclasses__())
    while todo:
        cls = todo.pop()
        todo.extend(cls.__subclasses__())
        if cls.__module__.split(".")[0] == "ada":
            names.add(cls.__name__)
    return names


def _build() -> ada.Assembly:
    hinge = BeamHinge("H1", [BeamHingeDofType("rz", "free")])
    bm1 = ada.Beam("Bm1", (0, 0, 0), (4, 0, 0), sec="IPE300", justification="flush_top")
    bm1.hinge1 = hinge
    bm2 = ada.Beam("Bm2", (0, 1.5, 0), (4, 1.5, 0), sec="IPE300")
    pl1 = ada.Plate.from_3d_points("Pl1", [(0, 3, 0), (4, 3, 0), (4, 4, 0), (0, 4, 0)], 0.01)
    p = ada.Part("P") / (bm1, bm2, pl1)
    p.add_mass(ada.MassPoint("M1", (2, 3.5, 0), 100.0))

    loads = p.concept_fem.loads
    lc1 = loads.add_load_case(
        LoadConceptCase(
            "LC1",
            loads=[
                LoadConceptPoint("P1", (2, 0, 0), (0, 0, -1e4), (0, 0, 0)),
                LoadConceptLine("L1", (0, 1.5, 0), (4, 1.5, 0), (0, 0, -1e3), (0, 0, -3e3)),
                LoadConceptSurface("S1", plate_ref=pl1, pressure=1e3, side="back"),
                LoadConceptSurface("S2", points=[(1, 3.25, 0), (3, 3.25, 0), (3, 3.75, 0)], pressure=1e3),
            ],
            design_condition=DesignCondition.OPERATING,
        )
    )
    lc2 = loads.add_load_case(
        LoadConceptCase("LC2", loads=[LoadConceptAccelerationField("G", (0, 0, -9.80665))], fem_loadcase_number=2)
    )
    loads.add_load_case_combination(
        LoadConceptCaseCombination(
            "LCC1",
            load_cases=[LoadConceptCaseFactored(lc1, 1.5), LoadConceptCaseFactored(lc2, 1.0)],
            design_condition=DesignCondition.OPERATING,
        )
    )
    cons = p.concept_fem.constraints
    cons.add_point_constraint(ConstraintConceptPoint("Sp1", (0, 0, 0), ConstraintConceptDofType.pinned()))
    cons.add_curve_constraint(
        ConstraintConceptCurve("Sc1", (0, 4, 0), (4, 4, 0), [ConstraintConceptDofType("dz", "fixed")])
    )
    return ada.Assembly("A") / p


def _attributes(xml_file) -> list[tuple[str, str, str]]:
    root = ET.parse(xml_file).getroot()
    return [(el.tag, k, v) for el in root.iter() for k, v in el.attrib.items()]


@pytest.mark.parametrize("streaming", [False, True], ids=["dom", "streaming"])
def test_no_attribute_is_an_enum_repr(tmp_path, streaming):
    xml_file = tmp_path / "model.xml"
    _build().to_genie_xml(xml_file, embed_sat=False, streaming=streaming)
    attributes = _attributes(xml_file)

    # the sweep is only worth something if the model reached the enum-carrying attributes
    tags = {tag for tag, _, _ in attributes}
    assert {"loadcase_basic", "loadcase_combination", "support_point", "support_curve", "point_mass"} <= tags

    # Collected after the write: an enum that reached the XML had its class loaded by then.
    enum_names = _ada_enum_names()
    assert {"DesignCondition", "Justification", "EquipRepr"} <= enum_names
    qualified = re.compile(rf"^({'|'.join(sorted(map(re.escape, enum_names)))})\.[A-Za-z_]\w*$")
    offenders = [(tag, k, v) for tag, k, v in attributes if qualified.match(v)]
    assert offenders == []


def test_design_condition_is_the_genie_token(tmp_path):
    """GeniE's own export writes ``design_condition="operating"`` on both kinds of load case."""
    xml_file = tmp_path / "model.xml"
    _build().to_genie_xml(xml_file, embed_sat=False)
    root = ET.parse(xml_file).getroot()
    lcs = root.findall("./model/analysis_domain/analyses/global/loadcases/*")
    assert sorted((lc.tag, lc.attrib["name"]) for lc in lcs) == [
        ("loadcase_basic", "LC1"),
        ("loadcase_basic", "LC2"),
        ("loadcase_combination", "LCC1"),
    ]
    assert {lc.attrib["design_condition"] for lc in lcs} == {"operating"}


@pytest.mark.parametrize(
    "value, expected",
    [("surface", "SURFACE"), ("panel", "PANEL"), ("SURFACE", "SURFACE"), ("coplanar", None)],
)
def test_merge_strategy_enum_is_read_by_value(value, expected):
    """A ``MergeStrategy`` member is the same request as its value spelled as a string."""
    from ada.cadit.gxml.write.stream_xml import _analytic_merge_strategy
    from ada.fem.formats.mesh_faces import MergeStrategy

    member = MergeStrategy(value.lower())
    got_str, got_enum = _analytic_merge_strategy(value), _analytic_merge_strategy(member)
    assert got_enum == got_str
    assert (got_enum.name if got_enum is not None else None) == expected
