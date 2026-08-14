"""ProceduralCatalog typed objects + resolver wiring.

These cover the DB-free surface: turning a catalog doc into an ada.Equipment /
TopoSystem, and feeding a slug->doc resolver into ProceduralBuilder. The live
``ProceduralCatalog.connect`` path is postgres-backed and exercised by the
DB-gated rest tests, not here.
"""

from __future__ import annotations

import ada
from ada.topo_model import EquipmentType, ProceduralBuilder, SystemTemplate

# A catalog equipment doc, exactly the shape build_equipment_from_catalog consumes.
PUMP_DOC = {
    "bbox": {"lx": 1.2, "ly": 0.8, "lz": 1.5},
    "mass": 750.0,
    "ifc_element_class": "IfcPump",
    "ports": [
        {
            "name": "discharge",
            "position": [0, 0, 1.5],
            "direction_vector": [0, 0, 1],
            "direction": "OUT",
            "category": "process",
        },
        {
            "name": "power",
            "position": [0.6, 0, 0.75],
            "direction_vector": [1, 0, 0],
            "direction": "IN",
            "category": "electrical",
        },
    ],
}


def test_equipment_type_to_equipment():
    et = EquipmentType(slug="my_pump", name="My Pump", doc=PUMP_DOC)
    eq = et.to_equipment("P1", origin=(2, 2, 3))
    assert isinstance(eq, ada.Equipment)
    assert eq.ifc_element_class == "IfcPump"
    assert {p.name for p in eq.ports} == {"discharge", "power"}


def test_equipment_type_extent_override():
    et = EquipmentType(slug="my_pump", name="My Pump", doc=PUMP_DOC)
    eq = et.to_equipment("P1", origin=(0, 0, 0), lx=2.0, ly=2.0, lz=2.0)
    # overrides win over the catalog bbox
    assert (eq.lx, eq.ly, eq.lz) == (2.0, 2.0, 2.0)


def test_system_template_to_system():
    st = SystemTemplate(slug="cw", name="Cooling Water", doc={"type": "piping", "medium": "water"})
    sys = st.to_system("CW", connections=[{"EQUIPMENT": "P1", "PORT": "discharge"}])
    assert sys.NAME == "CW" and sys.TYPE == "piping" and sys.MEDIUM == "water"
    assert sys.CONNECTIONS[0].EQUIPMENT == "P1"


def test_resolver_feeds_builder():
    """A slug->doc resolver (as ProceduralCatalog.equipment_resolver returns)
    lets ProceduralBuilder build a catalog equipment by DESCRIPTION slug."""
    catalog = {"my_pump": PUMP_DOC}
    doc = {
        "spaces": [{"NAME": "Cell1", "X": 0, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3}],
        "equipments": [
            {
                "NAME": "P1",
                "DESCRIPTION": "my_pump",
                "SPACE_NAME": "Cell1",
                "SPACE_LOC": "FLOOR",
                "X": 2,
                "Y": 2,
                "Z": 0,
                "LX": 1.2,
                "LY": 0.8,
                "LZ": 1.5,
                "COGx": 0,
                "COGy": 0,
                "COGz": 0.75,
                "massDry": 750,
                "massCont": 0,
            }
        ],
    }
    pb = ProceduralBuilder.from_dict(doc, equipment_resolver=catalog.get)
    pb.build_structure()
    pb.build_equipment()
    eq = pb.equipment_map["P1"]
    assert eq.ifc_element_class == "IfcPump"
    assert {p.name for p in eq.ports} == {"discharge", "power"}
