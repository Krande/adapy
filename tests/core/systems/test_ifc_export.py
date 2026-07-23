"""IFC export of equipment/systems: proper distribution elements, nested
ports, system groupings and port connectivity — verified by reading the file
back with ifcopenshell."""

from __future__ import annotations

import ifcopenshell
import pytest

from ada.topo_model import build_topo_model_with_systems


@pytest.fixture(scope="module")
def demo_ifc(tmp_path_factory) -> ifcopenshell.file:
    out = tmp_path_factory.mktemp("topo_ifc") / "topo_demo.ifc"
    a = build_topo_model_with_systems()
    a.to_ifc(out)
    return ifcopenshell.open(str(out))


def test_equipment_elements(demo_ifc):
    assert [e.Name for e in demo_ifc.by_type("IfcPump")] == ["Pump1"]
    assert [e.Name for e in demo_ifc.by_type("IfcTank")] == ["Tank1"]


def test_ports_nested_with_flow_directions(demo_ifc):
    nests = {rel.RelatingObject.Name: [p for p in rel.RelatedObjects] for rel in demo_ifc.by_type("IfcRelNests")}
    pump_ports = {p.Name: p.FlowDirection for p in nests["Pump1"]}
    assert pump_ports == {"suction": "SINK", "discharge": "SOURCE", "power": "SINK", "signal": "SOURCEANDSINK"}
    tank_ports = {p.Name: p.FlowDirection for p in nests["Tank1"]}
    assert tank_ports == {"inlet": "SINK", "outlet": "SOURCE", "signal": "SOURCEANDSINK"}
    assert all(p.is_a("IfcDistributionPort") for ports in nests.values() for p in ports)


def test_distribution_systems_predefined_types(demo_ifc):
    systems = {s.Name: s.PredefinedType for s in demo_ifc.by_type("IfcDistributionSystem")}
    assert systems == {"CoolingWater": "WATERSUPPLY", "PowerFeed": "ELECTRICAL"}


def test_system_groups_contain_segments_and_equipment(demo_ifc):
    groups = {}
    for rel in demo_ifc.by_type("IfcRelAssignsToGroup"):
        if rel.RelatingGroup.is_a("IfcDistributionSystem"):
            groups[rel.RelatingGroup.Name] = {e.is_a() for e in rel.RelatedObjects}
    assert {"IfcPipeSegment", "IfcPump", "IfcTank"} <= groups["CoolingWater"]
    assert {"IfcCableSegment", "IfcPump"} <= groups["PowerFeed"]


def test_cable_run_uses_cable_entities(demo_ifc):
    assert len(demo_ifc.by_type("IfcCableSegment")) > 0
    cable_names = {e.Name for e in demo_ifc.by_type("IfcCableSegment")}
    assert all(n.startswith("PowerFeed") for n in cable_names)
    # the cable run's bends are cable fittings, not pipe fittings
    assert all(n.startswith("CoolingWater") for n in {e.Name for e in demo_ifc.by_type("IfcPipeFitting")})


def test_rel_connects_ports(demo_ifc):
    rels = demo_ifc.by_type("IfcRelConnectsPorts")
    runs = {(r.RelatingPort.Name, r.RelatedPort.Name) for r in rels}
    assert ("discharge", "inlet") in runs
