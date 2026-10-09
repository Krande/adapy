"""Multipart FEM concatenation: fold several part-instance FEMs into one so the single-part
writers (Sesam/MED/Genie) can export a multi-instance model. Ids are renumbered to avoid
conflicts and set names are prefixed with the instance name."""

import pathlib
import re

import pytest

import ada
from ada.fem import (
    Bc,
    Constraint,
    FemSet,
    Interaction,
    InteractionProperty,
    Load,
    Mass,
    PredefinedField,
    StepImplicitStatic,
    Surface,
)
from ada.fem.concat import concatenate_fem_to_single_part, single_part_assembly
from ada.fem.exceptions.model_definition import DoesNotSupportMultiPart
from ada.fem.formats import conversion_report
from ada.fem.formats.utils import get_fem_model_from_assembly
from ada.fem.interactions import ContactTypes
from ada.fem.loads import LoadPressure
from ada.fem.outputs import HistOutput
from ada.materials.metals import CarbonSteel


def _part_with_fem(name, origin_x):
    pl = ada.Plate("pl", [(origin_x, 0), (origin_x + 1, 0), (origin_x + 1, 1), (origin_x, 1)], 0.01)
    p = ada.Part(name) / pl
    p.fem = pl.to_fem_obj(0.5, "shell")
    # A named node set referencing the first 3 nodes — same name across both parts on purpose.
    first_three = [p.fem.nodes.from_id(i) for i in sorted(n.id for n in p.fem.nodes)[:3]]
    p.fem.sets.add(FemSet("CLAMP", first_three, FemSet.TYPES.NSET, parent=p.fem))
    return p


def test_concatenate_multipart_fem():
    a = ada.Assembly("A")
    a.add_part(_part_with_fem("PartA", 0.0))
    a.add_part(_part_with_fem("PartB", 10.0))

    parts = [p for p in a.get_all_parts_in_assembly() if p.fem and len(p.fem.nodes) > 0]
    assert len(parts) == 2
    n_total = sum(len(p.fem.nodes) for p in parts)
    e_total = sum(len(p.fem.elements) for p in parts)
    # both parts independently number nodes from 1 -> ids collide pre-merge
    assert min(n.id for n in parts[0].fem.nodes) == min(n.id for n in parts[1].fem.nodes)

    merged = concatenate_fem_to_single_part(a)

    # The merge is NON-DESTRUCTIVE: a standalone part holds the combined FEM, and the source
    # assembly is left untouched (both parts keep their own FEM in the tree).
    still = [p for p in a.get_all_parts_in_assembly() if p.fem and len(p.fem.nodes) > 0]
    assert len(still) == 2
    assert merged not in a.get_all_parts_in_assembly()
    assert len(merged.fem.nodes) == n_total
    assert len(merged.fem.elements) == e_total

    # no duplicate node/element ids after the renumber
    nids = [n.id for n in merged.fem.nodes]
    eids = [e.id for e in merged.fem.elements]
    assert len(set(nids)) == len(nids)
    assert len(set(eids)) == len(eids)

    # both CLAMP sets survive, prefixed with their instance name (no merge-by-name), and each
    # resolves to 3 existing nodes.
    clamp_sets = [s for s in merged.fem.sets if s.name.endswith("_CLAMP")]
    assert len(clamp_sets) == 2
    assert {s.name for s in clamp_sets} == {"PartA_CLAMP", "PartB_CLAMP"}
    for s in clamp_sets:
        members = s.members
        assert len(members) == 3
        assert all(merged.fem.nodes.from_id(m.id) is not None for m in members)


# ── what the merge carries besides the mesh: supports, assembly-level data, steps ─────────────
#
# Two 1 x 1 m plates meshed at 0.5 m (9 nodes and 8 triangles each; ids 1..9 and 1..8 in both), PartA at
# x = 0, PartB at x = 10. Both start their ids at 1, so the merge shifts PartB's nodes by 9 and its
# elements by 8, and every set name both parts use ("edge", "tip", "plate") is prefixed with the part
# name. Measured on main before this change: the merge raised ``AttributeError: property 'fem_set' of
# 'Bc' object has no setter`` for any part-level Bc; past that, an assembly-level Bc was dropped and a
# step load on PartB's ``tip`` (PartB node 4, merged id 13) was written to a Sesam deck as
# ``BNLOAD ... 4.0`` -- PartA's node.


#: PartA's clamped edge (x = 0) and tip corner (x = 1, y = 1), by PartA's own node ids; PartB's are the same
#: ids in its own numbering. Read off the 0.5 m gmsh mesh of the plate.
EDGE_IDS = [2, 3, 6]
TIP_IDS = [4]
NODE_SHIFT = 9
ELEM_SHIFT = 8


def _plate_part(name: str, x0: float, first_id: int = 1, E: float | None = None) -> ada.Part:
    mat = {} if E is None else {"mat": ada.Material("S355", CarbonSteel("S355", E=E))}
    pl = ada.Plate("pl", [(x0, 0), (x0 + 1, 0), (x0 + 1, 1), (x0, 1)], 0.01, **mat)
    p = ada.Part(name) / pl
    p.fem = pl.to_fem_obj(0.5, "shell")
    if first_id != 1:
        p.fem.nodes.renumber(start_id=first_id)
        p.fem.elements.renumber(start_id=first_id)
    edge = [n for n in p.fem.nodes if abs(n.x - x0) < 1e-9]
    tip = [n for n in p.fem.nodes if abs(n.x - (x0 + 1)) < 1e-9 and abs(n.y - 1.0) < 1e-9]
    p.fem.sets.add(FemSet("edge", edge, FemSet.TYPES.NSET, parent=p.fem))
    p.fem.sets.add(FemSet("tip", tip, FemSet.TYPES.NSET, parent=p.fem))
    plate = p.fem.sets.add(FemSet("plate", list(p.fem.elements), FemSet.TYPES.ELSET, parent=p.fem))
    p.fem.add_surface(Surface("top", Surface.TYPES.ELEMENT, plate, parent=p.fem))
    p.fem.add_bc(Bc("fix", p.fem.nsets["edge"], [1, 2, 3, 4, 5, 6]))
    return p


def _two_plates() -> ada.Assembly:
    a = ada.Assembly("A")
    a.add_part(_plate_part("PartA", 0.0))
    a.add_part(_plate_part("PartB", 10.0))
    return a


def _corner_b(a: ada.Assembly) -> list:
    """PartB's far corner (x = 11, y = 0), as PartB's own node."""
    return [n for n in a.get_part("PartB").fem.nodes if abs(n.x - 11.0) < 1e-9 and abs(n.y) < 1e-9]


def _ids(fem_set) -> list[int]:
    return sorted(int(m.id) for m in fem_set.members)


def _merged_part(write_assembly: ada.Assembly) -> ada.Part:
    (merged,) = [p for p in write_assembly.get_all_parts_in_assembly() if len(p.fem.nodes) > 0]
    return merged


def test_the_two_plates_number_their_nodes_as_the_tests_below_assume():
    a = _two_plates()
    for p in a.get_all_parts_in_assembly():
        assert _ids(p.fem.nsets["edge"]) == EDGE_IDS
        assert _ids(p.fem.nsets["tip"]) == TIP_IDS
        assert max(int(n.id) for n in p.fem.nodes) == NODE_SHIFT
        assert max(int(e.id) for e in p.fem.elements) == ELEM_SHIFT


def test_a_part_bc_is_re_keyed_in_the_merged_fem():
    a = _two_plates()
    pa, pb = a.get_part("PartA"), a.get_part("PartB")
    merged = concatenate_fem_to_single_part(a)

    by_set = {bc.fem_set.name: _ids(bc.fem_set) for bc in merged.fem.bcs}
    assert by_set == {"PartA_edge": EDGE_IDS, "PartB_edge": [i + NODE_SHIFT for i in EDGE_IDS]}
    # One FEM holds one Bc per name (FEM.add_bc): the two parts' "fix" are prefixed like their sets.
    assert sorted(bc.name for bc in merged.fem.bcs) == ["PartA_fix", "PartB_fix"]
    for bc in merged.fem.bcs:
        assert bc.parent is merged.fem
        assert bc.fem_set.parent is merged.fem
        # ``FemSet.refs`` as the Bc constructor leaves it: the set lists the Bc that names it.
        assert [r for r in bc.fem_set.refs if isinstance(r, Bc)] == [bc]
    # The source parts keep their own Bcs on their own sets, and those sets list only them.
    for p in (pa, pb):
        (bc,) = p.fem.bcs
        assert bc.fem_set is p.fem.nsets["edge"]
        assert bc.parent is p.fem
        assert [r for r in bc.fem_set.refs if isinstance(r, Bc)] == [bc]


def test_an_assembly_level_bc_on_part_nodes_is_carried():
    a = _two_plates()
    corner = _corner_b(a)
    a.fem.add_bc(Bc("asm_fix", FemSet("asm_corner", corner, FemSet.TYPES.NSET), [3]))

    wa = single_part_assembly(a)
    merged = _merged_part(wa)
    bcs = merged.fem.bcs + wa.fem.bcs
    assert sorted(bc.name for bc in bcs) == ["PartA_fix", "PartB_fix", "asm_fix"]
    (asm,) = [bc for bc in bcs if bc.name == "asm_fix"]
    assert _ids(asm.fem_set) == [int(corner[0].id) + NODE_SHIFT]
    assert asm.fem_set.parent is merged.fem
    # source untouched
    assert a.fem.bcs[0].fem_set is a.fem.nsets["asm_corner"]
    assert _ids(a.fem.nsets["asm_corner"]) == [int(corner[0].id)]


def _tip_load_model(on_part: bool = False) -> ada.Assembly:
    a = _two_plates()
    pb = a.get_part("PartB")
    owner = pb.fem if on_part else a.fem
    step = owner.add_step(StepImplicitStatic("s", nl_geom=False, init_incr=100.0, total_time=100.0))
    step.add_load(Load("tipload", Load.TYPES.FORCE, -1000.0, dof=3, fem_set=pb.fem.nsets["tip"]))
    return a


def test_a_step_load_names_the_merged_set_and_ids():
    a = _tip_load_model()
    wa = single_part_assembly(a)
    (step,) = wa.fem.steps
    (load,) = step.loads
    assert load.fem_set.name == "PartB_tip"
    assert _ids(load.fem_set) == [TIP_IDS[0] + NODE_SHIFT]
    assert load.parent is step and step.parent is wa.fem
    # the user's step is untouched
    (src_load,) = a.fem.steps[0].loads
    assert src_load.fem_set is a.get_part("PartB").fem.nsets["tip"]
    assert src_load.parent is a.fem.steps[0]


def _bnload_nodes(fem_file: pathlib.Path) -> list[int]:
    """The node number (5th field) of every ``BNLOAD`` record of a Sesam deck."""
    records, current = [], None
    for line in fem_file.read_text().splitlines():
        if line[:8].strip():
            current = [] if line.startswith("BNLOAD") else None
            if current is not None:
                records.append(current)
        if current is not None:
            current.extend(float(v) for v in line[8:].split())
    return [int(r[4]) for r in records]


def test_a_sesam_deck_puts_the_part_b_load_on_part_b_s_node(tmp_path):
    a = _tip_load_model()
    a.to_fem("tipload", "sesam", scratch_dir=tmp_path, overwrite=True, execute=False)
    assert _bnload_nodes(tmp_path / "tipload" / "tiploadT1.FEM") == [TIP_IDS[0] + NODE_SHIFT]


def _fresh_set_load_model(part_b_first_id: int) -> tuple[ada.Assembly, list]:
    """A load on PartB's tip through a set no FEM holds: ``Step.add_load`` does not adopt a load's
    set (``examples/fem/sections_and_offsets.py`` builds its loads this way)."""
    a = ada.Assembly("A")
    a.add_part(_plate_part("PartA", 0.0))
    pb = a.add_part(_plate_part("PartB", 10.0, first_id=part_b_first_id))
    tip = list(pb.fem.nsets["tip"].members)
    step = a.fem.add_step(StepImplicitStatic("s", nl_geom=False, init_incr=100.0, total_time=100.0))
    step.add_load(Load("f", Load.TYPES.FORCE, -1000.0, dof=3, fem_set=FemSet("fresh_tip", tip, FemSet.TYPES.NSET)))
    assert step.loads[0].fem_set.parent is None
    return a, tip


@pytest.mark.parametrize("part_b_first_id, merged_tip", [(101, 104), (1, TIP_IDS[0] + NODE_SHIFT)])
def test_a_step_load_on_a_set_no_fem_holds_lands_on_its_members_part(part_b_first_id, merged_tip, tmp_path):
    """Measured before the fix: refused in both cases ("names set 'fresh_tip', which belongs to no
    part of the merged model"); on main the disjoint case wrote ``BNLOAD`` on 104 (right) and the
    colliding one on 4 (PartA's node)."""
    a, tip = _fresh_set_load_model(part_b_first_id)
    assert [int(n.id) for n in tip] == [TIP_IDS[0] + part_b_first_id - 1]

    wa = single_part_assembly(a)
    (load,) = wa.fem.steps[0].loads
    assert _ids(load.fem_set) == [merged_tip]
    assert load.fem_set.parent is _merged_part(wa).fem
    assert load.fem_set.name == "fresh_tip"
    # the user's set is untouched
    assert a.fem.steps[0].loads[0].fem_set.parent is None
    assert [int(n.id) for n in a.fem.steps[0].loads[0].fem_set.members] == [int(n.id) for n in tip]

    a.to_fem("fresh", "sesam", scratch_dir=tmp_path, overwrite=True, execute=False)
    assert _bnload_nodes(tmp_path / "fresh" / "freshT1.FEM") == [merged_tip]


def test_a_set_no_fem_holds_is_refused_when_its_members_belong_to_no_merged_part():
    a = _two_plates()
    elsewhere = _plate_part("NotInTheAssembly", 20.0)
    step = a.fem.add_step(StepImplicitStatic("s", nl_geom=False, init_incr=100.0, total_time=100.0))
    stray = FemSet("stray_tip", list(elsewhere.fem.nsets["tip"].members), FemSet.TYPES.NSET)
    step.add_load(Load("f", Load.TYPES.FORCE, -1000.0, dof=3, fem_set=stray))
    with pytest.raises(DoesNotSupportMultiPart, match=r"Load 'f'.*'stray_tip'.*Node 4.*no part of the merged model"):
        single_part_assembly(a)


def test_a_set_no_fem_holds_given_by_ids_is_refused():
    """Ids alone, with no FEM to resolve them in, say nothing of which part they are on."""
    a = _two_plates()
    step = a.fem.add_step(StepImplicitStatic("s", nl_geom=False, init_incr=100.0, total_time=100.0))
    step.add_load(Load("f", Load.TYPES.FORCE, -1000.0, dof=3, fem_set=FemSet("by_ids", [4], FemSet.TYPES.NSET)))
    with pytest.raises(DoesNotSupportMultiPart, match=r"Load 'f'.*'by_ids'.*by id only"):
        single_part_assembly(a)


def test_a_set_no_fem_holds_spanning_two_parts_is_offset_member_by_member():
    a = _two_plates()
    tips = [n for p in a.get_all_parts_in_assembly() for n in p.fem.nsets["tip"].members]
    step = a.fem.add_step(StepImplicitStatic("s", nl_geom=False, init_incr=100.0, total_time=100.0))
    step.add_load(Load("f", Load.TYPES.FORCE, -1000.0, dof=3, fem_set=FemSet("both_tips", tips, FemSet.TYPES.NSET)))
    (load,) = single_part_assembly(a).fem.steps[0].loads
    assert _ids(load.fem_set) == [TIP_IDS[0], TIP_IDS[0] + NODE_SHIFT]


def test_a_surface_no_fem_holds_is_carried_by_the_part_of_its_sets():
    a = _two_plates()
    pb = a.get_part("PartB")
    step = a.fem.add_step(StepImplicitStatic("s", nl_geom=False, init_incr=100.0, total_time=100.0))
    fresh = Surface("fresh_top", Surface.TYPES.ELEMENT, pb.fem.elsets["plate"])
    out = HistOutput("watch", [fresh], "contact", ["CSTRESS"])
    out.parent = step
    step.hist_outputs.append(out)
    assert fresh.parent is None

    wa = single_part_assembly(a)
    (watch,) = [h for h in wa.fem.steps[0].hist_outputs if h.name == "watch"]
    (surface,) = watch.fem_set
    assert surface.name == "fresh_top"
    assert surface.parent is _merged_part(wa).fem
    assert surface.fem_set.name == "PartB_plate"
    assert _ids(surface.fem_set) == [i + ELEM_SHIFT for i in range(1, ELEM_SHIFT + 1)]
    assert fresh.parent is None and fresh.fem_set is pb.fem.elsets["plate"]


def test_part_level_steps_reach_the_merged_fem():
    a = _tip_load_model(on_part=True)
    wa = single_part_assembly(a)
    merged = _merged_part(wa)
    (step,) = merged.fem.steps
    assert step.parent is merged.fem
    (load,) = step.loads
    assert load.fem_set.name == "PartB_tip"
    assert _ids(load.fem_set) == [TIP_IDS[0] + NODE_SHIFT]
    assert wa.fem.steps == []


def test_a_pressure_on_part_b_names_the_merged_surface_and_elements():
    a = _two_plates()
    pb = a.get_part("PartB")
    step = a.fem.add_step(StepImplicitStatic("s", nl_geom=False, init_incr=100.0, total_time=100.0))
    step.add_load(LoadPressure("q", 1000.0, pb.fem.surfaces["top"]))
    wa = single_part_assembly(a)
    merged = _merged_part(wa)
    (load,) = wa.fem.steps[0].loads
    assert load.surface.name == "PartB_top"
    assert merged.fem.surfaces["PartB_top"] is load.surface
    assert load.surface.fem_set.name == "PartB_plate"
    assert _ids(load.surface.fem_set) == [i + ELEM_SHIFT for i in range(1, ELEM_SHIFT + 1)]
    assert sorted(merged.fem.surfaces) == ["PartA_top", "PartB_top"]
    # the user's surface is untouched
    assert pb.fem.surfaces["top"].fem_set is pb.fem.elsets["plate"]
    assert pb.fem.surfaces["top"].name == "top"


def test_a_step_object_the_merge_cannot_re_key_is_refused_by_name():
    a = _two_plates()
    elsewhere = _plate_part("NotInTheAssembly", 20.0)
    step = a.fem.add_step(StepImplicitStatic("s", nl_geom=False, init_incr=100.0, total_time=100.0))
    out = HistOutput("watch", elsewhere.fem.nsets["tip"], "node", ["U3"])
    out.parent = step
    step.hist_outputs.append(out)
    with pytest.raises(DoesNotSupportMultiPart, match=r"step 's'.*'watch'.*'tip'"):
        single_part_assembly(a)


def _velocity(fem) -> PredefinedField:
    return PredefinedField("vel", PredefinedField.TYPES.VELOCITY, fem.nsets["tip"], dofs=[3], magnitude=[1.0])


def _contact(a: ada.Assembly) -> Interaction:
    pa, pb = a.get_part("PartA"), a.get_part("PartB")
    return Interaction(
        "contact",
        ContactTypes.SURFACE,
        pa.fem.surfaces["top"],
        pb.fem.surfaces["top"],
        InteractionProperty("ip"),
    )


def _mass_on_a_part_with_no_nodes() -> ada.Assembly:
    """PartB inside a part "Mid" that has no nodes of its own and holds a point mass on PartB's tip."""
    a = ada.Assembly("A")
    a.add_part(_plate_part("PartA", 0.0))
    mid = a.add_part(ada.Part("Mid"))
    pb = mid.add_part(_plate_part("PartB", 10.0))
    mid.fem.add_mass(Mass("m1", list(pb.fem.nsets["tip"].members), 10.0))
    return a


@pytest.mark.parametrize("what", ["predefined_field", "initial_state", "interaction"])
def test_what_the_merge_does_not_carry_is_refused_by_name(what):
    """The merge carries none of these; before the refusal it dropped them without a word."""
    a = _two_plates()
    pb = a.get_part("PartB")
    if what == "predefined_field":
        pb.fem.add_predefined_field(_velocity(pb.fem))
        match = r"'PartB' holds predefined field 'vel'"
    elif what == "initial_state":
        pb.fem.initial_state = PredefinedField(
            "ist", PredefinedField.TYPES.INITIAL_STATE, initial_state_file="x.odb", initial_state_part=pb
        )
        match = r"'PartB' holds initial state 'ist'"
    else:
        a.fem.add_interaction(_contact(a))
        match = r"'A' holds interaction 'contact'"
    with pytest.raises(DoesNotSupportMultiPart, match=match):
        single_part_assembly(a)


def test_an_element_on_a_part_with_no_nodes_is_refused_by_name():
    a = _mass_on_a_part_with_no_nodes()
    with pytest.raises(
        DoesNotSupportMultiPart, match=r"'Mid' holds 1 element\(s\) and 0 mass\(es\) on other parts' nodes"
    ):
        single_part_assembly(a)


def test_a_tie_between_two_parts_surfaces_names_the_merged_surfaces():
    """A constraint whose operands are surfaces: each re-pointed at its part's merged surface."""
    a = _two_plates()
    pa, pb = a.get_part("PartA"), a.get_part("PartB")
    a.fem.add_constraint(Constraint("tie", Constraint.TYPES.TIE, pa.fem.surfaces["top"], pb.fem.surfaces["top"]))

    merged = _merged_part(single_part_assembly(a))
    tie = merged.fem.constraints["tie"]
    assert (tie.m_set.name, tie.s_set.name) == ("PartA_top", "PartB_top")
    assert tie.m_set is merged.fem.surfaces["PartA_top"] and tie.s_set is merged.fem.surfaces["PartB_top"]
    assert _ids(tie.s_set.fem_set) == [i + ELEM_SHIFT for i in range(1, ELEM_SHIFT + 1)]
    # the user's constraint is untouched
    assert a.fem.constraints["tie"].m_set is pa.fem.surfaces["top"]
    assert a.fem.constraints["tie"].s_set is pb.fem.surfaces["top"]


def test_the_writers_own_merge_refuses_what_it_would_write_un_re_keyed():
    """A writer called directly with a multi-part assembly merges the parts itself and reads the
    assembly's own Bcs and steps from the unmerged model, where they name the source sets and ids."""
    a = _tip_load_model()
    with pytest.raises(DoesNotSupportMultiPart, match="tipload"):
        get_fem_model_from_assembly(a)


@pytest.mark.parametrize("fem_format", ["code_aster", "calculix", "sesam"])
def test_the_single_part_writers_write_two_supported_plates(fem_format, tmp_path):
    a = _two_plates()
    a.fem.add_bc(Bc("asm_fix", FemSet("asm_corner", _corner_b(a), FemSet.TYPES.NSET), [3]))
    step = a.fem.add_step(StepImplicitStatic("g", nl_geom=False, init_incr=100.0, total_time=100.0))
    step.add_load(ada.fem.LoadGravity("grav", -9.81))
    a.to_fem("two", fem_format, scratch_dir=tmp_path, overwrite=True, execute=False)

    if fem_format == "code_aster":
        comm = (tmp_path / "two" / "two.comm").read_text()
        for group in ("PartA_edge", "PartB_edge", "asm_corner"):
            assert f"GROUP_NO='{group}'" in comm or f'GROUP_NO="{group}"' in comm, group
    elif fem_format == "calculix":
        inp = (tmp_path / "two" / "two.inp").read_text().lower()
        for group in ("parta_edge", "partb_edge", "asm_corner"):
            assert re.search(rf"^\*boundary\s*\n\s*{group},", inp, re.M), group
    else:
        corner = int(_corner_b(a)[0].id) + NODE_SHIFT
        fem = (tmp_path / "two" / "twoT1.FEM").read_text()
        held = sorted(int(float(line.split()[1])) for line in fem.splitlines() if line.startswith("BNBCD"))
        assert held == sorted(EDGE_IDS + [i + NODE_SHIFT for i in EDGE_IDS] + [corner])


# -- two materials of one name ---------------------------------------------------------------------
#
# Every writer names a material by its name, and ``Materials.add`` hands back the material already held
# under a name. Measured before the fix (PartA "S355" E = 2.1e11, PartB "S355" E = 7.0e10): one
# ``mt_S355 = DEFI_MATERIAU(ELAS=_F(E=210000000000.0`` given to both parts' elsets, one CalculiX
# ``*Material, name=S355`` / ``2.100000E+11``, one Sesam ``MISOSEL`` -- PartB solved three times stiffer than
# modelled, without a word.


def _two_materials(e_b: float) -> ada.Assembly:
    a = ada.Assembly("A")
    a.add_part(_plate_part("PartA", 0.0, E=2.1e11))
    a.add_part(_plate_part("PartB", 10.0, E=e_b))
    step = a.fem.add_step(StepImplicitStatic("g", nl_geom=False, init_incr=100.0, total_time=100.0))
    step.add_load(ada.fem.LoadGravity("grav", -9.81))
    return a


def _section_material(fem, elset_name: str):
    (sec,) = [s for s in fem.sections if s.elset.name == elset_name]
    return sec.material


def test_two_materials_of_one_name_and_different_properties_stay_two():
    a = _two_materials(7.0e10)
    with conversion_report.collect() as report:
        merged = concatenate_fem_to_single_part(a)
    assert sorted((m.name, m.model.E) for m in merged.materials) == [("PartB_S355", 7.0e10), ("S355", 2.1e11)]
    assert _section_material(merged.fem, "PartA_elpl_sh").name == "S355"
    assert _section_material(merged.fem, "PartB_elpl_sh").name == "PartB_S355"
    assert _section_material(merged.fem, "PartB_elpl_sh") in merged.materials
    (finding,) = [f for f in report.findings if f.keyword == "Material"]
    assert finding.subject == "S355" and finding.details["new_name"] == "PartB_S355"
    # the user's materials are untouched
    assert [m.name for m in a.get_part("PartB").materials] == ["S355"]
    assert a.get_part("PartB").materials.get_by_name("S355").model.E == 7.0e10


def test_two_materials_of_one_name_and_equal_properties_stay_one():
    a = _two_materials(2.1e11)
    user_mats = [p.materials.get_by_name("S355") for p in a.get_all_parts_in_assembly()]
    user_refs = [list(m.refs) for m in user_mats]
    with conversion_report.collect() as report:
        merged = concatenate_fem_to_single_part(a)
    assert [m.name for m in merged.materials] == ["S355"]
    assert _section_material(merged.fem, "PartB_elpl_sh") is _section_material(merged.fem, "PartA_elpl_sh")
    assert [f for f in report.findings if f.keyword == "Material"] == []
    # The merged material lists both parts' plates; the user's each still list only their own.
    assert {id(r) for r in merged.materials.get_by_name("S355").refs} == {id(r) for refs in user_refs for r in refs}
    assert [list(m.refs) for m in user_mats] == user_refs


def _sesam_e_of_elements(fem_file: pathlib.Path) -> dict[int, float]:
    """Young's modulus each element of a Sesam deck is given: ``GELREF1`` element -> material number,
    ``MISOSEL`` material number -> E."""
    records, current = [], None
    for line in fem_file.read_text().splitlines():
        if line[:8].strip():
            current = [line[:8].strip()] if line.startswith(("MISOSEL", "GELREF1")) else None
            if current is not None:
                records.append(current)
        if current is not None:
            current.extend(float(v) for v in line[8:].split())
    e_of_mat = {int(r[1]): r[2] for r in records if r[0] == "MISOSEL"}
    return {int(r[1]): e_of_mat[int(r[2])] for r in records if r[0] == "GELREF1"}


@pytest.mark.parametrize("fem_format", ["code_aster", "calculix", "sesam"])
def test_each_part_s_elements_get_their_own_material_in_the_deck(fem_format, tmp_path):
    a = _two_materials(7.0e10)
    a.to_fem("tm", fem_format, scratch_dir=tmp_path, overwrite=True, execute=False)
    if fem_format == "code_aster":
        # (The writer cuts material names to five characters first: "PartB_S355" is "PartB" there.)
        comm = (tmp_path / "tm" / "tm.comm").read_text()
        e_of = {c: float(e) for c, e in re.findall(r"^(\w+) = DEFI_MATERIAU\(\s*ELAS=_F\(E=([\d.e+]+),", comm, re.M)}
        mater = dict((g, c) for c, g in re.findall(r'_F\(MATER=\((\w+),\), GROUP_MA="(\w+)"\)', comm))
        assert len(e_of) == 2
        assert {g: e_of[c] for g, c in mater.items()} == {"PartA_elpl_sh": 2.1e11, "PartB_elpl_sh": 7.0e10}
    elif fem_format == "calculix":
        inp = (tmp_path / "tm" / "tm.inp").read_text()
        assert re.search(r"^\*Shell Section, elset=PartA_elpl_sh, material=S355$", inp, re.M)
        assert re.search(r"^\*Shell Section, elset=PartB_elpl_sh, material=PartB_S355$", inp, re.M)
        assert re.search(r"^\*Material, name=S355\n\*Elastic\n 2.100000E\+11,", inp, re.M)
        assert re.search(r"^\*Material, name=PartB_S355\n\*Elastic\n 7.000000E\+10,", inp, re.M)
    else:
        e_of = _sesam_e_of_elements(tmp_path / "tm" / "tmT1.FEM")
        assert e_of == {**{i: 2.1e11 for i in range(1, 9)}, **{i: 7.0e10 for i in range(9, 17)}}


def _edge_load_on_part_b(a: ada.Assembly):
    """A line load along PartB's far edge (x = 11), one segment per shell element on it: ``LoadLine`` names its
    elements directly, not through a set."""
    from ada.fem.loads import LineLoadSegment, LoadLine

    pb = a.get_part("PartB")
    segs = []
    for el in pb.fem.elements:
        xs = [n.x for n in el.nodes]
        corners = len(xs)
        for k in range(corners):
            if abs(xs[k] - 11.0) < 1e-9 and abs(xs[(k + 1) % corners] - 11.0) < 1e-9:
                segs.append(LineLoadSegment(el, (0.0, 0.0, -1000.0), (0.0, 0.0, -1000.0), edge=k + 1))
    assert segs
    return LoadLine("edge_q", segs)


def test_a_line_load_on_part_b_loads_part_b_s_merged_elements():
    """The merge moved sets and surfaces onto the merged ids, but a ``LoadLine``'s segments kept PartB's own
    elements: the writers then wrote PartB's ids -- PartA's elements in the merged numbering (measured: a beam
    line load on the second of two cantilevers stopped Code_Aster at <MODELISA7_77>). Each segment is now on its
    element's merged copy: the id moved by PartB's offset, the same nodes' positions."""
    a = _two_plates()
    load = _edge_load_on_part_b(a)
    step = a.fem.add_step(StepImplicitStatic("s", nl_geom=False, init_incr=100.0, total_time=100.0))
    step.add_load(load)
    merged = _merged_part(single_part_assembly(a))
    (written,) = [ld for st in merged.get_assembly().fem.steps for ld in st.loads]
    assert len(written.segments) == len(load.segments)
    merged_ids = {int(e.id) for e in merged.fem.elements}
    for old, new in zip(load.segments, written.segments):
        assert new.elem.id == old.elem.id + ELEM_SHIFT and new.elem.id in merged_ids
        assert [tuple(n.p) for n in new.elem.nodes] == [tuple(n.p) for n in old.elem.nodes]
        assert (new.q1, new.q2, new.edge) == (old.q1, old.q2, old.edge)
    # the user's load is untouched
    assert all(s.elem.parent is a.get_part("PartB").fem for s in load.segments)


def test_a_line_load_on_an_element_of_no_merged_part_is_refused_by_name():
    a = _two_plates()
    elsewhere = _two_plates()  # a model of its own: its PartB is in no part of ``a``
    step = a.fem.add_step(StepImplicitStatic("s", nl_geom=False, init_incr=100.0, total_time=100.0))
    step.add_load(_edge_load_on_part_b(elsewhere))
    with pytest.raises(DoesNotSupportMultiPart, match=r"edge_q.*loads element \d+, which belongs to no part"):
        single_part_assembly(a)
