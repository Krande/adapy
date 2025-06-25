import xml.etree.ElementTree as ET

import ada


def test_roundtrip_xml(fem_files, tmp_path):
    original_xml_file = fem_files / "sesam/xml_all_basic_props.xml"
    new_xml = tmp_path / "basic_props.xml"

    a = ada.from_genie_xml(original_xml_file)

    global_constraints = a.concept_fem.constraints.get_global_constraint_concepts()
    assert len(global_constraints.point_constraints) == 3

    global_loads = a.concept_fem.loads.get_global_load_concepts()
    assert len(global_loads.load_cases) == 0

    a.to_genie_xml(new_xml)


def test_create_sesam_xml_from_mixed(mixed_model, tmp_path):
    xml_file = tmp_path / "mixed_xml_model.xml"

    mixed_model.to_genie_xml(xml_file)


def test_create_sesam_xml_with_plate(tmp_path):
    pl = ada.Plate("pl", [(0, 0), (10, 0), (10, 10), (0, 10)], 0.1)
    a = ada.Assembly("a") / pl
    a.to_genie_xml(tmp_path / "plate.xml", embed_sat=True)


def test_create_groups_split_across_parts(tmp_path):
    p1 = ada.Part("P1") / (ada.Beam("bm1", (0, 0, 0), (1, 0, 0), "IPE200"))
    p2 = ada.Part("P2") / (ada.Beam("bm2", (0, 0, 1), (1, 0, 1), "IPE200"))
    p1.add_group("group1", [p1.beams[0]])
    p2.add_group("group1", [p2.beams[0]])

    a = ada.Assembly("a") / (p1, p2)

    dest = tmp_path / "groups_split_across_parts.xml"

    a.to_genie_xml(dest, embed_sat=False)

    tree = ET.parse(dest)
    root = tree.getroot()
    sets = root.find("./model/structure_domain/sets")

    assert sets is not None

    assert len(sets.findall("./set")) == 1

    assert len(sets.findall("./set/concepts/concept")) == 2
    assert sets.find("./set/concepts/concept[@concept_ref='bm1']") is not None
    assert sets.find("./set/concepts/concept[@concept_ref='bm2']") is not None


def test_check_placement_of_parts(tmp_path):
    start = (0, 0, 0)
    end = (5, 0, 0)
    end2 = (10, 0, 0)
    end3 = (15, 0, 0)

    bm_plates_1 = ada.Beam("bm3", end2, end3, "BG200x200x8x8").to_plates()

    # Part 1
    bm1 = ada.Beam("bm1", start, end, "IPE200")
    bm2 = ada.Beam("bm2", end, end2, "IPE200")
    p1 = ada.Part("P1") / (bm1, bm2, *bm_plates_1)
    p1.add_mass(ada.MassPoint("m1", end, 100))
    p1.concept_fem.constraints.add_point_constraint(
        ada.ConstraintConceptPoint("bc1", start, ada.ConstraintConceptDofType.encastre())
    )
    lc1 = p1.concept_fem.loads.add_load_case(
        ada.LoadConceptCase(
            "LC1",
            fem_loadcase_number=32,
            loads=[
                ada.LoadConceptPoint(name="PointLoad1", position=end, force=(0, 0, -50), moment=(0, 0, 0)),
                ada.LoadConceptLine(
                    name="LineLoad1",
                    start_point=start,
                    end_point=end,
                    intensity_start=(0, 0, -50),
                    intensity_end=(0, 0, -50),
                ),
                ada.LoadConceptSurface(
                    name="SurfaceLoad1",
                    points=[(0, 0, 0), (3, 0, 0), (3, 3, 0), (0, 3, 0)],
                    pressure=2000,
                    side="front",
                ),
                ada.LoadConceptAccelerationField(
                    name="AccelFieldLoad1",
                    acceleration=(0, 0, -9.81),
                    include_self_weight=True,
                    rotational_field=ada.RotationalAccelerationField(start, bm1.xvec, 0.01, 0.04),
                ),
            ],
        )
    )
    p1.concept_fem.loads.add_load_case_combination(
        ada.LoadConceptCaseCombination(
            "LCC1",
            load_cases=[ada.LoadConceptCaseFactored(lc1, factor=1.5, phase=0)],
            design_condition="operating",
            complex_type="static",
            convert_load_to_mass=False,
            global_scale_factor=1.0,
            equipments_type="line_load",
        )
    )
    p1.concept_fem.constraints.add_rigid_link(
        ada.ConstraintConceptRigidLink(
            "rigid_link1",
            end2,
            ada.RigidLinkRegion.from_center_and_offset(end2, (1, 1, 1)),
            ada.ConstraintConceptDofType.encastre("dependent"),
        )
    )

    # Part 2
    bm3 = ada.Beam("bm3", start, end, "IPE200")
    bm4 = ada.Beam("bm4", end, end2, "IPE200")
    bm_plates_2 = ada.Beam("bm4", end2, end3, "BG200x200x8x8").to_plates()
    p2 = ada.Part("P2") / (bm3, bm4, *bm_plates_2)
    p2.placement = ada.Placement(origin=(0, 0, 10))
    p2.add_mass(ada.MassPoint("m2", end, 100))
    p2.concept_fem.constraints.add_point_constraint(
        ada.ConstraintConceptPoint("bc2", start, ada.ConstraintConceptDofType.encastre())
    )
    lc2 = p2.concept_fem.loads.add_load_case(
        ada.LoadConceptCase(
            "LC2",
            fem_loadcase_number=33,
            loads=[
                ada.LoadConceptPoint(name="PointLoad2", position=end, force=(0, 0, -50), moment=(0, 0, 0)),
                ada.LoadConceptLine(
                    name="LineLoad2",
                    start_point=start,
                    end_point=end,
                    intensity_start=(0, 0, -50),
                    intensity_end=(0, 0, -50),
                ),
                ada.LoadConceptSurface(
                    name="SurfaceLoad2",
                    points=[(0, 0, 0), (3, 0, 0), (3, 3, 0), (0, 3, 0)],
                    pressure=2000,
                    side="front",
                ),
                ada.LoadConceptAccelerationField(
                    name="AccelFieldLoad2",
                    acceleration=(0, 0, -9.81),
                    include_self_weight=True,
                    rotational_field=ada.RotationalAccelerationField(start, bm1.xvec, 0.01, 0.04)
                ),
            ],
        )
    )
    p2.concept_fem.loads.add_load_case_combination(
        ada.LoadConceptCaseCombination(
            "LCC2",
            load_cases=[ada.LoadConceptCaseFactored(lc2, factor=1.5, phase=0)],
            design_condition="operating",
            complex_type="static",
            convert_load_to_mass=False,
            global_scale_factor=1.0,
            equipments_type="line_load",
        )
    )
    p2.concept_fem.constraints.add_rigid_link(
        ada.ConstraintConceptRigidLink(
            "rigid_link2",
            end2,
            ada.RigidLinkRegion.from_center_and_offset(end2, (1, 1, 1)),
            ada.ConstraintConceptDofType.encastre("dependent"),
        )
    )

    a = ada.Assembly("a") / (p1, p2)
    dest = a.to_genie_xml(tmp_path / "offset_parts.xml", embed_sat=False)

    with open(dest, "r") as f:
        xml_str = f.read()

    xml_root = ET.fromstring(xml_str)

    # Test 1: Validate support points exist and have correct positions
    support_points = xml_root.findall(".//support_point")
    assert len(support_points) == 2, "Should have 2 support points (bc1 and bc2)"

    # Check bc1 (Part 1) - should be at origin (0,0,0)
    bc1_point = next(sp for sp in support_points if sp.get("name") == "bc1")
    bc1_position = bc1_point.find(".//position")
    assert bc1_position.get("x") == "0.0"
    assert bc1_position.get("y") == "0.0"
    assert bc1_position.get("z") == "0.0"

    # Check bc2 (Part 2) - should be at (0,0,10) due to placement offset
    bc2_point = next(sp for sp in support_points if sp.get("name") == "bc2")
    bc2_position = bc2_point.find(".//position")
    assert bc2_position.get("x") == "0.0"
    assert bc2_position.get("y") == "0.0"
    assert bc2_position.get("z") == "10.0"

    # Test 2: Validate boundary conditions for support points
    for sp in support_points:
        boundary_conditions = sp.findall(".//boundary_condition")
        assert len(boundary_conditions) == 6, "Should have 6 DOF constraints (encastre)"

        # Check all DOFs are fixed for encastre condition
        dofs = {bc.get("dof"): bc.get("constraint") for bc in boundary_conditions}
        expected_dofs = {"dx": "fixed", "dy": "fixed", "dz": "fixed", "rx": "fixed", "ry": "fixed", "rz": "fixed"}
        assert dofs == expected_dofs, "DOF constraints should match encastre condition"

    # Test 3: Validate rigid links exist and have correct positions
    rigid_links = xml_root.findall(".//support_rigid_link")
    assert len(rigid_links) == 2, "Should have 2 rigid links"

    # Check rigid_link1 (Part 1) - should be at end2 (10,0,0)
    rl1 = next(rl for rl in rigid_links if rl.get("name") == "rigid_link1")
    rl1_position = rl1.find(".//position")
    assert rl1_position.get("x") == "10.0"
    assert rl1_position.get("y") == "0.0"
    assert rl1_position.get("z") == "0.0"

    # Check rigid_link2 (Part 2) - should be at end2 + offset (10,0,10)
    rl2 = next(rl for rl in rigid_links if rl.get("name") == "rigid_link2")
    rl2_position = rl2.find(".//position")
    assert rl2_position.get("x") == "10.0"
    assert rl2_position.get("y") == "0.0"
    assert rl2_position.get("z") == "10.0"

    # Test 4: Validate rigid link attributes
    for rl in rigid_links:
        assert rl.get("include_all_edges") == "true"
        assert rl.get("rotation_dependent") == "true"

        # Check footprint box region exists
        footprint_box = rl.find(".//footprint_box")
        assert footprint_box is not None, "Rigid link should have footprint_box"

        lower_corner = footprint_box.find("lower_corner")
        upper_corner = footprint_box.find("upper_corner")
        assert lower_corner is not None and upper_corner is not None

    # Test 5: Validate rigid link boundary conditions
    for rl in rigid_links:
        boundary_conditions = rl.findall(".//boundary_condition")
        assert len(boundary_conditions) == 6, "Rigid link should have 6 DOF constraints"

        # Check all DOFs are dependent for rigid link
        dofs = {bc.get("dof"): bc.get("constraint") for bc in boundary_conditions}
        expected_dofs = {
            "dx": "dependent",
            "dy": "dependent",
            "dz": "dependent",
            "rx": "dependent",
            "ry": "dependent",
            "rz": "dependent",
        }
        assert dofs == expected_dofs, "Rigid link DOFs should be dependent"

    # Test 6: Validate mass points exist (if they create XML elements)
    point_masses = xml_root.findall(".//point_mass")
    assert len(point_masses) == 2, "Should have 2 point masses"

    mass_m1 = [m for m in point_masses if m.get("name") == "m1"][0]
    # Assert that the position of m1 is at end
    mass_m1_position = mass_m1.find(".//position")
    assert mass_m1_position.get("x") == "5.0"
    assert mass_m1_position.get("y") == "0.0"
    assert mass_m1_position.get("z") == "0.0"

    mass_m2 = [m for m in point_masses if m.get("name") == "m2"][0]
    # Assert that the position of m1 is at end
    mass_m2_position = mass_m2.find(".//position")
    assert mass_m2_position.get("x") == "5.0"
    assert mass_m2_position.get("y") == "0.0"
    assert mass_m2_position.get("z") == "10.0"

    # Test 7: Validate load cases exist
    load_cases = xml_root.findall(".//loadcase_basic")
    assert len(load_cases) >= 2, "Should have at least 2 load cases (LC1 and LC2)"

    # Test 8: Validate load case combinations
    load_combinations = xml_root.findall(".//loadcase_combination")
    assert len(load_combinations) >= 2, "Should have at least 2 load combinations"

    # Test 9: Validate beams exist with correct placement
    beams = xml_root.findall(".//straight_beam")
    assert len(beams) == 4, "Should have 4 beams"
    # Should have beams from both parts, check if positions are correctly offset

    # Test 10: Validate local coordinate systems exist
    local_systems = xml_root.findall(".//local_system")
    assert len(local_systems) > 0, "Should have local coordinate systems"

    # Test 11: Validate structure domain contains proper elements
    structure_domain = xml_root.find(".//structure_domain")
    assert structure_domain is not None, "Should have structure_domain"

    structures = structure_domain.find("structures")
    assert structures is not None, "Should have structures element"

    # Test 12: Validate materials and sections are present
    properties = structure_domain.find("properties")
    assert properties is not None, "Should have properties section"

    materials = properties.findall(".//material")
    sections = properties.findall(".//section")
    assert len(materials) > 0, "Should have materials defined"
    assert len(sections) > 0, "Should have sections defined"

    # Do not add these in prod
    a.show()
    from ada.cadit.gxml.utils import start_genie

    start_genie(dest)
