from __future__ import annotations

import ada
from ada.fem.concept.constraints import (
    ConstraintConceptCurve,
    ConstraintConceptDofType,
    ConstraintConceptPoint,
)
from ada.fem.concept.loads import (
    LoadConceptCase,
    LoadConceptCaseCombination,
    LoadConceptCaseFactored,
    LoadConceptGravity,
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
                LoadConceptGravity(
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
