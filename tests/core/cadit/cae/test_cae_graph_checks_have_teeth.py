"""Every graph check is shown to reject a broken script, not merely to pass a good one.

A checker that has never been shown a defect is indistinguishable from `assert True`. So each check in
`cae_script_graph` is paired here with one deliberate defect injected into the kernel-validated
specimen, and the check is asserted to reject it on its own -- not merely that *something* in the full
pass complained.

Three failure modes this project has already been bitten by are guarded explicitly:

* a green baseline is asserted first, so a mutation cannot "pass" against an already-broken specimen;
* every anchor is asserted to appear **exactly once** in the specimen, so a mutation cannot silently
  edit nothing, or edit the wrong occurrence;
* the mutation table is asserted non-empty and its names unique, so a run that collected no
  mutations fails instead of reporting success.

**What the graph pass deliberately cannot catch:** reversing ``n1`` to ``-n1``. The reversed vector is
still unit and still perpendicular to the member, so no static reading of the script can tell the two
apart -- and no linear-elastic deflection can either, because every second moment of area is invariant
under a 180 degree rotation of the section. The sign is pinned by
`test_cae_orientation_convention.py`, which requires the CAE writer's ``n1`` to equal the INP
writer's, and that file carries the sign-flip mutation.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from .cae_script_graph import (
    ALL_CHECKS,
    CaeGraphError,
    ScriptGraph,
    check_analysis_references_resolve,
    check_emitted_script,
    check_every_member_is_sectioned_and_oriented,
    check_every_part_instanced_once,
    check_failure_is_signalled,
    check_members_are_located_by_a_cylinder_spanning_them,
    check_names_unique,
    check_orientation_vectors,
    check_plate_faces_are_sectioned,
    check_preamble,
    check_profiles_and_materials_defined_before_use,
    check_python_floor,
    check_sections_defined_before_use,
    check_shell_sections_are_complete,
    check_the_acis_body_is_imported_for_every_plate_part,
)


@dataclass(frozen=True)
class Defect:
    """One writer defect, expressed as a single unique text edit of one specimen.

    ``specimen`` names which one. ``"frame"`` is the hand-written, kernel-validated beams-only
    script in ``files/``; ``"plates"`` is the writer's own output for ``plate_model``, because a
    hand-written plate specimen would have to carry a hand-written ACIS body beside it and the plate
    checks read the ``PLATES`` table the writer computes *from* that body.
    """

    name: str
    anchor: str
    replacement: str
    caught_by: object
    message: str
    specimen: str = "frame"

    def apply(self, source: str) -> str:
        return source.replace(self.anchor, self.replacement)


_ORIENT_COL1 = "region=region_col1, method=N1_COSINES, n1=(1.0, 0.0, 0.0)"
_GIRDER_CYLINDER = "center1=(-0.01, 0.0, 4.0), center2=(6.01, 0.0, 4.0), radius=0.001"
_GIRDER_LOOKUP = "edges_girder = p.edges.getByBoundingCylinder({})".format(_GIRDER_CYLINDER)
_INSTANCE = 'a.Instance(name="Frame-1", part=p, dependent=ON)'


def wrapped(*arguments: str) -> str:
    """Anchor text for a call black has wrapped one argument per line, at one indent level.

    An anchor that spans lines is still exact text -- black's output is deterministic -- and
    building it from the arguments keeps it readable and keeps the indentation in one place. A
    rewrap shows up as `test_each_anchor_matches_the_specimen_exactly_once` failing, which is what
    that test is for: a mutation that has quietly stopped mutating proves nothing.
    """
    return "\n".join("        " + argument for argument in arguments)


DEFECTS = (
    # --- orientation ---------------------------------------------------------------------------
    Defect(
        "n1_along_the_member_axis",
        _ORIENT_COL1,
        "region=region_col1, method=N1_COSINES, n1=(0.0, 0.0, 1.0)",
        check_orientation_vectors,
        "not perpendicular",
    ),
    Defect(
        "n1_not_a_unit_vector",
        _ORIENT_COL1,
        "region=region_col1, method=N1_COSINES, n1=(1.0, 1.0, 0.0)",
        check_orientation_vectors,
        "not 1",
    ),
    Defect(
        "orientation_method_changed",
        _ORIENT_COL1,
        "region=region_col1, method=N2_COSINES, n1=(1.0, 0.0, 0.0)",
        check_orientation_vectors,
        "phase 1 emits N1_COSINES",
    ),
    # --- coverage ------------------------------------------------------------------------------
    Defect(
        "section_assignment_dropped",
        '    p.SectionAssignment(region=region_girder, sectionName="sec_BG200")\n',
        "",
        check_every_member_is_sectioned_and_oriented,
        "no section for",
    ),
    Defect(
        "orientation_dropped",
        "    p.assignBeamSectionOrientation(region=region_girder, method=N1_COSINES, n1=(0.0, 1.0, 0.0))\n",
        "",
        check_every_member_is_sectioned_and_oriented,
        "cover different regions",
    ),
    Defect(
        "member_drawn_but_never_located",
        "    p.WirePolyLine(points=(((3.0, 0.0, 4.0), (3.0, 2.0, 0.0)),), mergeType=IMPRINT, meshable=ON)\n",
        "",
        check_every_member_is_sectioned_and_oriented,
        "members are drawn but",
    ),
    Defect(
        "zero_length_member",
        "points=(((0.0, 0.0, 0.0), (0.0, 0.0, 4.0)),)",
        "points=(((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),)",
        check_every_member_is_sectioned_and_oriented,
        "zero-length member",
    ),
    # --- names ---------------------------------------------------------------------------------
    Defect(
        "section_name_emitted_twice",
        'm.BeamSection(name="sec_BG200", profile=',
        'm.BeamSection(name="sec_IPE300", profile=',
        check_names_unique,
        "is emitted twice",
    ),
    Defect(
        "set_name_emitted_twice",
        'p.Set(name="brace", edges=edges_brace)',
        'p.Set(name="col1", edges=edges_brace)',
        check_names_unique,
        "is emitted twice",
    ),
    Defect(
        "name_carries_a_dot",
        'p.Set(name="girder", edges=edges_girder)',
        'p.Set(name="gird.er", edges=edges_girder)',
        check_names_unique,
        "contains a dot",
    ),
    # --- references ----------------------------------------------------------------------------
    Defect(
        "profile_never_created",
        'profile="BG200", material="S355"',
        'profile="NOT_DEFINED", material="S355"',
        check_profiles_and_materials_defined_before_use,
        "which is never created",
    ),
    Defect(
        "material_never_created",
        'profile="HP200", material="S355"',
        'profile="HP200", material="S420"',
        check_profiles_and_materials_defined_before_use,
        "which is never created",
    ),
    Defect(
        "section_never_created",
        'sectionName="sec_HP200"',
        'sectionName="sec_HP200x"',
        check_sections_defined_before_use,
        "which is never created",
    ),
    # --- assembly ------------------------------------------------------------------------------
    Defect(
        "part_instanced_twice",
        _INSTANCE,
        _INSTANCE + '\n    a.Instance(name="Frame-2", part=p, dependent=ON)',
        check_every_part_instanced_once,
        "is instanced 2 times",
    ),
    Defect(
        "part_never_instanced",
        _INSTANCE,
        "pass  # the instance the assembly needs was never emitted",
        check_every_part_instanced_once,
        "is instanced 0 times",
    ),
    # --- member location -----------------------------------------------------------------------
    Defect(
        "located_by_findAt_at_the_midpoint",
        _GIRDER_LOOKUP,
        "edges_girder = p.edges.findAt(((3.0, 0.0, 4.0),))",
        check_members_are_located_by_a_cylinder_spanning_them,
        "locates its member with findAt",
    ),
    Defect(
        "cylinder_does_not_overshoot_the_ends",
        _GIRDER_CYLINDER,
        "center1=(0.0, 0.0, 4.0), center2=(6.0, 0.0, 4.0), radius=0.001",
        check_members_are_located_by_a_cylinder_spanning_them,
        "does not overshoot",
    ),
    Defect(
        "cylinder_misses_the_member",
        _GIRDER_CYLINDER,
        "center1=(-0.01, 0.0, 3.9), center2=(6.01, 0.0, 3.9), radius=0.001",
        check_members_are_located_by_a_cylinder_spanning_them,
        "does not lie on any member",
    ),
    Defect(
        "region_is_empty",
        'p.SectionAssignment(region=region_brace, sectionName="sec_HP200")',
        'p.SectionAssignment(region=(), sectionName="sec_HP200")',
        check_members_are_located_by_a_cylinder_spanning_them,
        "empty region",
    ),
    # --- the analysis: supports, loads and the step chain --------------------------------------
    Defect(
        "load_in_a_step_that_does_not_exist",
        'm.ConcentratedForce(name="px_F", createStepName="lc1"',
        'm.ConcentratedForce(name="px_F", createStepName="nowhere"',
        check_analysis_references_resolve,
        "neither 'Initial' nor a step this script creates",
    ),
    Defect(
        "load_region_never_built",
        '_analysis_region(a, "TOP", "Frame-1", ((6.0, 0.0, 4.0),), "TOP")',
        '_analysis_region(a, "TOPP", "Frame-1", ((6.0, 0.0, 4.0),), "TOPP")',
        check_analysis_references_resolve,
        "which is never created",
    ),
    # These two anchors span several lines, because black wraps a six-keyword call one argument
    # per line and the specimen is formatted like the rest of the repo. See `wrapped`.
    Defect(
        "a_support_that_restrains_nothing",
        wrapped("u1=0.0,", "u2=0.0,", "u3=0.0,", "ur1=0.0,", "ur2=0.0,", "ur3=0.0,"),
        wrapped("u1=UNSET,", "u2=UNSET,", "u3=UNSET,", "ur1=UNSET,", "ur2=UNSET,", "ur3=UNSET,"),
        check_analysis_references_resolve,
        "leaves every DOF UNSET",
    ),
    Defect(
        "a_step_chain_that_is_not_a_chain",
        wrapped('name="lc1",', 'previous="Initial",'),
        wrapped('name="lc1",', 'previous="lc0",'),
        check_analysis_references_resolve,
        "neither 'Initial' nor a step created before it",
    ),
    # --- preamble, failure signalling, syntax floor ---------------------------------------------
    Defect(
        "caeModules_import_dropped",
        "from caeModules import *\n",
        "",
        check_preamble,
        "never imports caeModules",
    ),
    Defect(
        "failure_exits_zero",
        "sys.exit(1)",
        "sys.exit(0)",
        check_failure_is_signalled,
        "non-zero status",
    ),
    Defect(
        "result_sidecar_dropped",
        'RESULT_PATH = "specimen_frame_cae.cae_build_result.json"',
        'RESULT_PATH = "specimen_frame_cae.buildlog.txt"',
        check_failure_is_signalled,
        "cae_build_result.json",
    ),
    Defect(
        "f_string_breaks_the_python_floor",
        '"{0} of {1} edges have a section assignment".format(len(covered), len(p.edges))',
        'f"{len(covered)} of {len(p.edges)} edges have a section assignment"',
        check_python_floor,
        "f-string",
    ),
    Defect(
        "multi_arg_print_without_print_function",
        "from __future__ import print_function\n\n",
        "",
        check_python_floor,
        "would print a tuple",
    ),
    # --- plates: the shell section, the faces it covers, and the body they came from ------------
    Defect(
        "shell_section_thickness_is_not_a_length",
        "model.HomogeneousShellSection(name='sh_0p012_S355', material='S355', thickness=0.012)",
        "model.HomogeneousShellSection(name='sh_0p012_S355', material='S355', thickness=0.0)",
        check_shell_sections_are_complete,
        "which is not a length",
        specimen="plates",
    ),
    Defect(
        "shell_section_material_never_created",
        "model.HomogeneousShellSection(name='sh_0p01_S355', material='S355', thickness=0.01)",
        "model.HomogeneousShellSection(name='sh_0p01_S355', material='S275', thickness=0.01)",
        check_shell_sections_are_complete,
        "which is never created",
        specimen="plates",
    ),
    Defect(
        "a_plate_gets_the_wrong_thickness",
        "sectionName='sh_0p012_S355', offsetType=MIDDLE_SURFACE",
        "sectionName='sh_0p01_S355', offsetType=MIDDLE_SURFACE",
        check_plate_faces_are_sectioned,
        "thick in the model and its shell section",
        specimen="plates",
    ),
    Defect(
        "a_plate_is_never_given_a_shell_section",
        "    part_0.SectionAssignment(region=plate_region_0_1, sectionName='sh_0p012_S355', "
        "offsetType=MIDDLE_SURFACE,\n"
        "                          thicknessAssignment=FROM_SECTION)\n",
        "",
        check_plate_faces_are_sectioned,
        "located and never given a shell section",
        specimen="plates",
    ),
    Defect(
        "a_plate_is_sectioned_somewhere_other_than_its_mid_surface",
        "sectionName='sh_0p01_S355', offsetType=MIDDLE_SURFACE",
        "sectionName='sh_0p01_S355', offsetType=TOP_SURFACE",
        check_plate_faces_are_sectioned,
        "moves the reference surface silently",
        specimen="plates",
    ),
    Defect(
        "a_plate_is_located_twice",
        "faces_0_1 = _plate_faces('PlateFrame', 'deck', part_0, PLATES['PlateFrame'][1]['points'])",
        "faces_0_1 = _plate_faces('PlateFrame', 'bulkhead', part_0, PLATES['PlateFrame'][1]['points'])",
        check_plate_faces_are_sectioned,
        "is located twice",
        specimen="plates",
    ),
    Defect(
        "the_acis_body_is_rescaled_by_its_own_file",
        ".sat'), scaleFromFile=OFF)",
        ".sat'), scaleFromFile=ON)",
        check_the_acis_body_is_imported_for_every_plate_part,
        "must not be rescaled by the file",
        specimen="plates",
    ),
    Defect(
        "the_acis_body_is_looked_for_in_the_working_directory",
        "_acis_0 = mdb.openAcis(_beside_script('specimen_plates_PlateFrame.sat'), scaleFromFile=OFF)",
        "_acis_0 = mdb.openAcis('specimen_plates_PlateFrame.sat', scaleFromFile=OFF)",
        check_the_acis_body_is_imported_for_every_plate_part,
        "whatever directory the run started in",
        specimen="plates",
    ),
    Defect(
        "the_imported_body_arrives_as_several_parts",
        "geometryFile=_acis_0, combine=True",
        "geometryFile=_acis_0, combine=False",
        check_the_acis_body_is_imported_for_every_plate_part,
        "has to arrive as one part",
        specimen="plates",
    ),
)


@pytest.fixture
def specimens(specimen_source, plate_specimen_source) -> dict:
    """The sources a defect can be injected into, by the name its ``specimen`` field carries."""
    return {"frame": specimen_source, "plates": plate_specimen_source}


def test_the_specimen_is_accepted(specimen_source):
    """The green baseline. Without this a mutation could 'fail' on an already-broken specimen."""
    graph = check_emitted_script(specimen_source, name="specimen_frame_cae.py")

    assert len(graph.by_method("WirePolyLine")) == 3
    assert len(graph.by_method("SectionAssignment")) == 3
    assert len(graph.by_method("assignBeamSectionOrientation")) == 3
    assert len(graph.profile_calls()) == 3


def test_every_check_in_the_pass_has_a_defect_that_exercises_it():
    """A check nobody injected a defect for is a check nobody has ever seen work."""
    exercised = {defect.caught_by for defect in DEFECTS}
    unexercised = sorted(check.__name__ for check in ALL_CHECKS if check not in exercised)

    assert unexercised == [], "no injected defect exercises {}".format(unexercised)


def test_the_defect_table_is_populated_and_unambiguous():
    """Guards a run that collects nothing, and a table with two entries under one name."""
    names = [defect.name for defect in DEFECTS]

    assert len(DEFECTS) >= 20
    assert sorted(names) == sorted(set(names))


@pytest.mark.parametrize("defect", DEFECTS, ids=lambda d: d.name)
def test_each_anchor_matches_the_specimen_exactly_once(defect, specimens):
    """A mutation that matched nothing -- or matched twice -- would prove nothing about the checker."""
    source = specimens[defect.specimen]
    assert source.count(defect.anchor) == 1, "anchor for {!r} is not unique".format(defect.name)
    assert defect.apply(source) != source


@pytest.mark.parametrize("defect", DEFECTS, ids=lambda d: d.name)
def test_the_named_check_rejects_the_defect(defect, specimens):
    """The declared check must reject it on its own, so 'which check caught it' is unambiguous."""
    mutated = defect.apply(specimens[defect.specimen])
    graph = ScriptGraph(mutated, name="mutated/" + defect.name)

    with pytest.raises(CaeGraphError) as caught:
        defect.caught_by(graph)

    assert defect.message in str(caught.value)


@pytest.mark.parametrize("defect", DEFECTS, ids=lambda d: d.name)
def test_the_whole_pass_rejects_the_defect(defect, specimens):
    """And the pass a caller actually runs must reject it too, whatever order the checks run in."""
    with pytest.raises(CaeGraphError):
        check_emitted_script(defect.apply(specimens[defect.specimen]), name="mutated/" + defect.name)


# --------------------------------------------------------------------------------------------------
# A checker with teeth also has to keep its mouth shut about valid scripts. Each entry below is a
# minimal script the pass must *accept*; the first one is a real false positive this check used to
# produce.

_STACKED_COLUMNS_IN_REVERSE_ORDER = """from __future__ import print_function

import sys

from abaqus import *
from abaqusConstants import *
from caeModules import *

RESULT_PATH = "stack.cae_build_result.json"
m = mdb.models["Model-1"]
p = m.Part(name="Stack", dimensionality=THREE_D, type=DEFORMABLE_BODY)
p.WirePolyLine(points=(((0.0, 0.0, 0.0), (0.0, 0.0, 4.0)),), mergeType=IMPRINT, meshable=ON)
p.WirePolyLine(points=(((0.0, 0.0, 4.0), (0.0, 0.0, 8.0)),), mergeType=IMPRINT, meshable=ON)
m.Material(name="S355")
m.IProfile(name="IPE300", l=0.15, h=0.3, b1=0.15, b2=0.15, t1=0.0107, t2=0.0107, t3=0.0071)
m.BeamSection(name="sec", profile="IPE300", material="S355", integration=DURING_ANALYSIS)
edges_upper = p.edges.getByBoundingCylinder(center1=(0.0, 0.0, 3.999), center2=(0.0, 0.0, 8.001), radius=0.001)
region_upper = p.Set(name="col_upper", edges=edges_upper)
p.SectionAssignment(region=region_upper, sectionName="sec")
p.assignBeamSectionOrientation(region=region_upper, method=N1_COSINES, n1=(1.0, 0.0, 0.0))
edges_lower = p.edges.getByBoundingCylinder(center1=(0.0, 0.0, -0.001), center2=(0.0, 0.0, 4.001), radius=0.001)
region_lower = p.Set(name="col_lower", edges=edges_lower)
p.SectionAssignment(region=region_lower, sectionName="sec")
p.assignBeamSectionOrientation(region=region_lower, method=N1_COSINES, n1=(1.0, 0.0, 0.0))
a = m.rootAssembly
a.Instance(name="Stack-1", part=p, dependent=ON)
sys.exit(1)
"""

ACCEPTED = {"stacked_columns_in_reverse_order": _STACKED_COLUMNS_IN_REVERSE_ORDER}


@pytest.mark.parametrize("name", sorted(ACCEPTED))
def test_a_valid_script_is_accepted(name):
    """Stacked columns are ordinary, and two of them are collinear.

    Each lies on the other's cylinder axis, so a check that matched a cylinder to the first collinear
    segment it met and then judged the end caps rejected this script with "does not overshoot its
    member's ends" -- a false positive, and a misleading one. It appeared only when the regions were not
    emitted in the same order as the wires, which is why the writer's own output did not show it.
    """
    check_emitted_script(ACCEPTED[name], name=name)
