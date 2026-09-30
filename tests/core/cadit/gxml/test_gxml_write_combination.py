"""Where adapy writes a load-case combination, and with what factors.

GeniE has two kinds: a combination made with ``LoadCombination()`` belongs to the model and GeniE
exports it under ``analyses/global`` -- a ``loadcase_combination`` among the load cases, and a
``combinations/combination`` holding the factored cases. One made with
``LoadCombination(Analysis1)`` belongs to that analysis and goes under ``analyses/analysis``.
adapy has no analysis, so it writes the first kind. GeniE V9.2-01 imports it with both cases and
their factors, and exports it again exactly here; ``tests/fem/test_genie_gxml_beam_loads.py``
checks that against GeniE itself. This pins the XML half.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import ada
from ada.fem.concept.loads import (
    LoadConceptAccelerationField,
    LoadConceptCase,
    LoadConceptCaseCombination,
    LoadConceptCaseFactored,
    LoadConceptLine,
)

GLOBAL = "./model/analysis_domain/analyses/global"


def test_global_combination_is_where_genie_exports_one(tmp_path):
    bm = ada.Beam("Bm1", (0, 0, 0), (4, 0, 0), sec="IPE300")
    p = ada.Part("P") / bm
    loads = p.concept_fem.loads
    lc_u = loads.add_load_case(
        LoadConceptCase(
            "LC_u",
            loads=[LoadConceptLine("L", (0, 0, 0), (4, 0, 0), (0, 0, -1e3), (0, 0, -1e3))],
            fem_loadcase_number=4,
        )
    )
    lc_g = loads.add_load_case(
        LoadConceptCase("LC_g", loads=[LoadConceptAccelerationField("G", (0, 0, -9.80665))], fem_loadcase_number=16)
    )
    loads.add_load_case_combination(
        LoadConceptCaseCombination(
            "LCC1", load_cases=[LoadConceptCaseFactored(lc_u, 1.5), LoadConceptCaseFactored(lc_g, 1.0)]
        )
    )
    xml_file = tmp_path / "model.xml"
    (ada.Assembly("A") / p).to_genie_xml(xml_file, embed_sat=False)
    root = ET.parse(xml_file).getroot()

    assert root.findall("./model/analysis_domain/analyses/analysis") == []
    lcc = root.find(f"{GLOBAL}/loadcases/loadcase_combination")
    assert lcc.attrib == {
        "name": "LCC1",
        "design_condition": "operating",
        "complex_type": "static",
        "convert_load_to_mass": "false",
        "global_scale_factor": "1.0",
    }
    combos = root.findall(f"{GLOBAL}/combinations/combination")
    assert [c.attrib["combination_ref"] for c in combos] == ["LCC1"]
    factored = [
        (c.attrib["loadcase_ref"], float(c.attrib["factor"])) for c in combos[0].findall("./loadcases/loadcase")
    ]
    assert factored == [("LC_u", 1.5), ("LC_g", 1.0)]
