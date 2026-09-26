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
    check_emitted_script,
    check_every_member_is_sectioned_and_oriented,
    check_every_part_instanced_once,
    check_failure_is_signalled,
    check_members_are_located_by_a_cylinder_spanning_them,
    check_names_unique,
    check_orientation_vectors,
    check_preamble,
    check_profiles_and_materials_defined_before_use,
    check_python_floor,
    check_sections_defined_before_use,
)


@dataclass(frozen=True)
class Defect:
    """One writer defect, expressed as a single unique text edit of the specimen."""

    name: str
    anchor: str
    replacement: str
    caught_by: object
    message: str

    def apply(self, source: str) -> str:
        return source.replace(self.anchor, self.replacement)


_ORIENT_COL1 = "region=region_col1, method=N1_COSINES, n1=(1.0, 0.0, 0.0)"
_GIRDER_CYLINDER = "center1=(-0.01, 0.0, 4.0), center2=(6.01, 0.0, 4.0), radius=0.001"
_GIRDER_LOOKUP = "edges_girder = p.edges.getByBoundingCylinder({})".format(_GIRDER_CYLINDER)
_INSTANCE = 'a.Instance(name="Frame-1", part=p, dependent=ON)'

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
)


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
def test_each_anchor_matches_the_specimen_exactly_once(defect, specimen_source):
    """A mutation that matched nothing -- or matched twice -- would prove nothing about the checker."""
    assert specimen_source.count(defect.anchor) == 1, "anchor for {!r} is not unique".format(defect.name)
    assert defect.apply(specimen_source) != specimen_source


@pytest.mark.parametrize("defect", DEFECTS, ids=lambda d: d.name)
def test_the_named_check_rejects_the_defect(defect, specimen_source):
    """The declared check must reject it on its own, so 'which check caught it' is unambiguous."""
    mutated = defect.apply(specimen_source)
    graph = ScriptGraph(mutated, name="mutated/" + defect.name)

    with pytest.raises(CaeGraphError) as caught:
        defect.caught_by(graph)

    assert defect.message in str(caught.value)


@pytest.mark.parametrize("defect", DEFECTS, ids=lambda d: d.name)
def test_the_whole_pass_rejects_the_defect(defect, specimen_source):
    """And the pass a caller actually runs must reject it too, whatever order the checks run in."""
    with pytest.raises(CaeGraphError):
        check_emitted_script(defect.apply(specimen_source), name="mutated/" + defect.name)
