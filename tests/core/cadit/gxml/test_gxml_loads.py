from __future__ import annotations

from xml.etree import ElementTree as ET

import ada
from ada import Beam, Plate
from ada.cadit.gxml.write.write_bcs import add_support_curve, add_support_point
from ada.cadit.gxml.write.write_load_case import add_loadcase, add_loadcase_combination, add_loadcase_to_combination
from ada.cadit.gxml.write.write_loads import add_line_load, add_point_load, add_surface_load_plate, \
    add_surface_load_polygon, add_gravity_load

def pp_example(root, part):

    create_point_load = False
    create_line_load = False
    create_surface_load = False
    create_gravity_load = False
    create_support_curve = False
    create_support_point = False

    # todo testing adding load case
    # Add Load Case
    global_elem = root.find("./model/analysis_domain/analyses/global")
    lc_elem = add_loadcase(global_elem, "LC1")

    structure_domain = root.find("./model/structure_domain")
    structures_elem = ET.SubElement(structure_domain, "structures")

    # todo testing adding line load for all beams
    for bm in part.get_all_physical_objects(by_type=Beam):


        if create_line_load:
            # Add line load
            line_load = add_line_load(
                global_elem, lc_elem, name=f"LLoad_{bm.name}",
                start_point=bm.n1,
                end_point=bm.n2,
                intensity_start=(0, 0, 200),
                intensity_end=(0, 0, 100)
            )


        if create_point_load:
            # Add point load
            point_load = add_point_load(
                global_elem,
                lc_elem,
                name=f"PLoad_{bm.name}",
                position=bm.n1,
                force=(300, 0, 0),
                moment=(0, 0, 0)
            )

    # todo tesing surface loads from plate name
    #  this will not work now since plates are handled in separate js
    if create_surface_load:
        surface_load = add_surface_load_plate(
            global_elem,
            lc_elem,
            name="SLoad2",
            plate_ref="mini_fw3_room1_f1_i2_j1",
            pressure=99,
            side="front"
        )

    if create_surface_load:
        # todo testing adding surface load for all plates defined by coordinates
        for pl in part.get_all_physical_objects(by_type=Plate):
            # todo tesing surface loads from coordinates
            points = pl.nodes
            surface_load = add_surface_load_polygon(
                global_elem,
                lc_elem,
                name=f"SLoad_{pl.name}",
                points=points,
                pressure=100000
            )

    if create_gravity_load:
        # todo testing gravity loads
        add_gravity_load(global_elem, lc_elem)

    # todo testing add load case combination
    # Add load case combination named "LCC2"
    lcc_elem = add_loadcase_combination(
        global_elem,
        name="LCC2",
        design_condition="operating",
        complex_type="static",
        convert_load_to_mass=False,
        global_scale_factor=1.5,
        equipments_type="line_load"
    )


    lc_elem2 = add_loadcase(global_elem, "LC2")
    # todo testing add load case to load case combination
    # Example to add LC1 to the combination LCC2
    add_loadcase_to_combination(global_elem, lcc_elem, lc_elem, factor=0.7, phase=0)
    add_loadcase_to_combination(global_elem, lcc_elem, lc_elem2, factor=1.3, phase=0)

    #add_analysis(part) #todo equivalent to step in ada.Part / in Genie Analysis Activity
    '''
        # js code
        Analysis1 = Analysis(true);
        Analysis1.add(MeshActivity());
        Analysis1.add(LinearAnalysis());
        Analysis1.step(2).useSestra10(true);
        Analysis1.add(LoadResultsActivity());
        Analysis1.setActive();
    '''

    # todo create xml function for analysis activity
    '''
        <analysis name="Analysis1" active="true" active_loadcase="LC1">
            <activities>
                <create_mesh beams_as_members="true" smart_load_combinations="true" write_load_combination_on_first_level_as_bsell="false" write_computed_load_cases_into_combination_bsell="true" pile_boundary_condition="pile_soil_interaction" use_partial_mesher="true" include_loads_on_mesh="false" needs_apply_loads="false" needs_remesh_loads="true" multithreaded_load_applier="true" multithreaded_mesher="false" write_fem_file="false" needs_fem_file_write="false" lock_meshed_concepts_after_partial_meshing="true" regenerate_mesh_option="conditional" exclude_include_subset_option="include" force_reuse_external_node_and_element_numbers="false" node_numbers_from_joint_name="false" element_numbers_from_beam_name="false" length_unit="m" force_unit="N" tempdiff_unit="delC">
                    <partial_remesh_handler prefer_NxM="false" allow_triangles="true" use_second_order="false" use_drilling="false" eliminate_internal_vertices="true" eliminate_internal_edges="true" use_uniformized_face_parametrization="false" is_remesh_required="false" short_edge_rel_length="-1" short_edge_abs_length="-1" pile_remeshing_required="false" super_element_type="1" super_element_type_top="1">
                        <edge_mesher edge_mesh_strategy="uniform_distribution" />
                        <face_mesher face_mesh_strategy="sesam_quad_mesher" />
                    </partial_remesh_handler>
                </create_mesh>
                <linear_sestra_run generate_input="true" data_check_only="false" eigenvalue_analysis="false" eigenvalue_solver="lanczos" eigenvalue_num_modes="10" eigenvalue_shift="0" eigenvalue_modal_mass_factors="true" eigenvalue_mass_matrix="false" warp_correction="true" multifront_order="0" continue_on_error="false" use_multifront_solver_LDL="false" use_subset="false" resultFileFormat="SIN_Norsam" force_sestra10="true" stress_stiffening="false" dynamic_analysis="false" dynamic_analysis_type="directIntegration" dynamic_domain="dyTimeDomain" dynamic_static_back_substitution="true" dynamic_alpha1="0.01" dynamic_alpha2="0.001" dynamic_use_modal_damping="true" dynamic_modal_damping="0.02" dynamic_time_step="0.1" dynamic_adaptive_time_stepping="false" dynamic_time_step_tolerance="0.01" dynamic_use_cyclic_loads="true" dynamic_steady_state_detection="false" dynamic_steady_state_tolerance="0.01" dynamic_max_cycles="100" dynamic_store_cycles="1" dynamic_store_reaction_forces="false" dynamic_use_fft_method="true" dynamic_fft_number="128" esl_option="opNone" esl_times_option="eslInputLoads" esl_factor="1" esl_load_comb="0" esl_store_results="false" esl_text_format="false" include_loads_from_interface_files="true" include_beam_element_forces_and_moments="true" include_shell_element_stresses="true">
                    <dynamic_reaction_forces_center x="0" y="0" z="0" />
                </linear_sestra_run>
                <load_results sin_file_option="match_load_and_result_case" />
            </activities>
            <loadcases auto_import="true" />
            <runtime_statuses>
                <status activity_ref="Analysis1.step(1)" activity_status="0" />
                <status activity_ref="Analysis1.step(1).step(1)" activity_status="0" />
                <status activity_ref="Analysis1.step(1).step(2)" activity_status="0" />
                <status activity_ref="Analysis1.step(1).step(3)" activity_status="0" />
                <status activity_ref="Analysis1.step(1).step(4)" activity_status="0" />
                <status activity_ref="Analysis1.step(2)" activity_status="0" />
                <status activity_ref="Analysis1.step(3)" activity_status="0" />
            </runtime_statuses>
            <completion_statuses>
                <status activity_ref="Analysis1.step(1)" activity_status="0" />
                <status activity_ref="Analysis1.step(1).step(1)" activity_status="0" />
                <status activity_ref="Analysis1.step(1).step(2)" activity_status="0" />
                <status activity_ref="Analysis1.step(1).step(3)" activity_status="0" />
                <status activity_ref="Analysis1.step(1).step(4)" activity_status="0" />
                <status activity_ref="Analysis1.step(2)" activity_status="0" />
                <status activity_ref="Analysis1.step(3)" activity_status="0" />
            </completion_statuses>
            <activity_start_time>
                <start_time activity_ref="Analysis1.step(1)" activity_start_time="0" />
                <start_time activity_ref="Analysis1.step(1).step(1)" activity_start_time="0" />
                <start_time activity_ref="Analysis1.step(1).step(2)" activity_start_time="0" />
                <start_time activity_ref="Analysis1.step(1).step(3)" activity_start_time="0" />
                <start_time activity_ref="Analysis1.step(1).step(4)" activity_start_time="0" />
                <start_time activity_ref="Analysis1.step(2)" activity_start_time="0" />
                <start_time activity_ref="Analysis1.step(3)" activity_start_time="0" />
            </activity_start_time>
            <activity_end_time>
                <end_time activity_ref="Analysis1.step(1)" activity_end_time="0" />
                <end_time activity_ref="Analysis1.step(1).step(1)" activity_end_time="0" />
                <end_time activity_ref="Analysis1.step(1).step(2)" activity_end_time="0" />
                <end_time activity_ref="Analysis1.step(1).step(3)" activity_end_time="0" />
                <end_time activity_ref="Analysis1.step(1).step(4)" activity_end_time="0" />
                <end_time activity_ref="Analysis1.step(2)" activity_end_time="0" />
                <end_time activity_ref="Analysis1.step(3)" activity_end_time="0" />
            </activity_end_time>
        </analysis>
    </analyses>
    '''

    #add_loads(loads, part)

    # todo add boundary conditions

    #root = ET.Element("model", {"name": "test"})
    #structure_domain = ET.SubElement(root, "structure_domain")
    #structures = ET.SubElement(structure_domain, "structures")
    #structure_elem = ET.SubElement(structures, "structure")

    # Support Curve Example
    if create_support_curve:
        add_support_curve(
            structures_elem=structures_elem,
            name="Sc1",
            start_pos=(0, 0, 0),
            end_pos=(0, 10, 0),
            dof_constraints={
                "dx": {"constraint": "fixed"},
                "dy": {"constraint": "free"},
                "dz": {"constraint": "prescribed"},
                "rx": {"constraint": "dependent"},
                "ry": {"constraint": "super"},
                "rz": {"constraint": "spring", "stiffness": 421}
            }
        )

    # Support Point Example
    if create_support_point:
        add_support_point(
            structures_elem=structures_elem,
            name="Sp1",
            pos=(10, 10, 10),
            dof_constraints={
                "dx": {"constraint": "fixed"},
                "dy": {"constraint": "free"},
                "dz": {"constraint": "super"},
                "rx": {"constraint": "spring", "stiffness": 1123},
                "ry": {"constraint": "dependent"},
                "rz": {"constraint": "spring", "stiffness": 1232}
            }
        )


def test_new_features(root, part):
    a = ada.Assembly()
    a.to_genie_xml("temp/output", writer_postprocessor=pp_example)