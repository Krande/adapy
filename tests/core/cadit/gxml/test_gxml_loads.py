from __future__ import annotations

import ada
from ada.fem.concept.constraints import (
    ConstraintConceptCurve,
    ConstraintConceptDofType,
    ConstraintConceptPoint,
)
from ada.fem.concept.loads import (
    LoadConceptAccelerationField,
    LoadConceptCase,
    LoadConceptCaseCombination,
    LoadConceptCaseFactored,
    LoadConceptLine,
    LoadConceptPoint,
    LoadConceptSurface,
)


def test_new_features():
    bm1 = ada.Beam(name="Beam1", n1=(0, 0, 0), n2=(10, 0, 0), sec="IPE300")
    pl1 = ada.Plate(name="Plate1", points=[(0, 0), (10, 0), (10, 10), (0, 10)], t=0.2)
    p = ada.Part(name="TestPart") / (bm1, pl1)
    lc1 = p.concept_fem.loads.add_load_case(
        LoadConceptCase(
            "LC1",
            loads=[
                LoadConceptPoint(name="PointLoad1", position=(10, 0, 0), force=(100, 0, 0), moment=(0, 0, 0)),
                LoadConceptLine(
                    name="LineLoad1",
                    start_point=(2, 2, 0),
                    end_point=(8, 2, 0),
                    intensity_start=(50, 0, 0),
                    intensity_end=(50, 0, 0),
                ),
                LoadConceptSurface(
                    name="SurfaceLoad1",
                    plate_ref=pl1,
                    pressure=2000,
                    side="front",
                ),
                LoadConceptSurface(
                    name="SurfaceLoad2",
                    points=[(0, 0, 0), (3, 0, 0), (3, 3, 0), (0, 3, 0)],
                    pressure=2000,
                    side="front",
                ),
                LoadConceptAccelerationField(
                    name="GravityLoad1",
                    acceleration=(0, 0, -9.81),
                ),
            ],
        )
    )
    p.concept_fem.loads.add_load_case_combination(
        LoadConceptCaseCombination(
            "LCC1",
            load_cases=[LoadConceptCaseFactored(lc1, factor=1.5, phase=0)],
            design_condition="operating",
            complex_type="static",
            convert_load_to_mass=False,
            global_scale_factor=1.0,
            equipments_type="line_load",
        )
    )
    p.concept_fem.constraints.add_point_constraint(
        ConstraintConceptPoint(
            "bc1",
            (0, 10, 0),
            [
                ConstraintConceptDofType(dof="dx", constraint_type="fixed"),
                ConstraintConceptDofType(dof="dy", constraint_type="fixed"),
                ConstraintConceptDofType(dof="dz", constraint_type="fixed"),
            ],
        )
    )
    p.concept_fem.constraints.add_point_constraint(
        ConstraintConceptPoint(
            "bc2",
            (10, 10, 0),
            [
                ConstraintConceptDofType(dof="dx", constraint_type="fixed"),
                ConstraintConceptDofType(dof="dy", constraint_type="fixed"),
                ConstraintConceptDofType(dof="dz", constraint_type="fixed"),
            ],
        )
    )
    p.concept_fem.constraints.add_curve_constraint(
        ConstraintConceptCurve(
            "bc3",
            start_pos=(0, 0, 0),
            end_pos=(10, 0, 0),
            dof_constraints=[
                ConstraintConceptDofType(dof="dx", constraint_type="fixed"),
                ConstraintConceptDofType(dof="dy", constraint_type="fixed"),
                ConstraintConceptDofType(dof="dz", constraint_type="free"),
                ConstraintConceptDofType(dof="rx", constraint_type="free"),
                ConstraintConceptDofType(dof="ry", constraint_type="free"),
            ],
        )
    )
    a = ada.Assembly() / p
    a.to_genie_xml("temp/output.xml")


def _env_loads(include_self_weight, rotational_field=None):
    import xml.etree.ElementTree as ET

    from ada.cadit.gxml.write.write_loads import add_acceleration_field_load

    global_elem = ET.Element("global")
    lc_elem = ET.Element("loadcase_basic", {"name": "LC1"})
    add_acceleration_field_load(global_elem, lc_elem, (0, 0, -9.81), include_self_weight, rotational_field)
    return {el.tag: el.get("include_selfweight") for el in global_elem.find("loads/environmental_loads")}


def test_gravity_and_rotation_field_self_weight_flags_are_separate():
    """GeniE holds an "include self weight" flag for the gravity field and one for the rotation field; the writer
    put the gravity field's on both."""
    from ada.fem.concept.loads import RotationalAccelerationField

    rot_off = RotationalAccelerationField((0, 0, 0), (0, 0, 1), 0.01, 0.04, include_self_weight=False)
    assert _env_loads(True, rot_off) == {
        "gravity_load": "true",
        "dummy_rotation_field": "false",
        "rotation_field": "false",
    }
    rot_on = RotationalAccelerationField((0, 0, 0), (0, 0, 1), 0.01, 0.04)
    assert _env_loads(False, rot_on) == {
        "gravity_load": "false",
        "dummy_rotation_field": "true",
        "rotation_field": "true",
    }
    # no rotation field: the dummy one carries GeniE's own "true", whatever the gravity flag says
    assert _env_loads(False) == {"gravity_load": "false", "dummy_rotation_field": "true"}


def test_the_rotation_field_self_weight_flag_reaches_the_xml(tmp_path):
    import xml.etree.ElementTree as ET

    from ada.fem.concept.loads import RotationalAccelerationField

    bm = ada.Beam("bm1", (0, 0, 0), (1, 0, 0), "IPE200")
    p = ada.Part("P") / bm
    rot = RotationalAccelerationField((0, 0, 0), (0, 0, 1), 0.01, 0.04, include_self_weight=False)
    p.concept_fem.loads.add_load_case(
        LoadConceptCase("LC1", loads=[LoadConceptAccelerationField("G", (0, 0, -9.81), True, rot)])
    )
    dst = tmp_path / "rot.xml"
    (ada.Assembly("A") / p).to_genie_xml(dst)
    env = ET.parse(dst).getroot().find(".//environmental_loads")
    assert {el.tag: el.get("include_selfweight") for el in env} == {
        "gravity_load": "true",
        "dummy_rotation_field": "false",
        "rotation_field": "false",
    }
