"""What the writer does with a model's supports, loads and analysis step -- and what it refuses.

Licence-free: everything here reads the emitted text and the plan behind it. Whether the calls
are *right* is settled by running them, in ``test_cae_licensed_acceptance.py``.

The first test in this file is not about the writer at all. It pins **where adapy keeps a
concept model's supports and loads**, because that turned out to be two places and not one, and
because the answer is load-bearing for every other test here: the two ``Bc`` records are on the
*part's* FEM while the ``Step`` carrying the loads is on the *assembly's*, so a writer that
gathered only the emitted part's own subtree would emit the frame's fixed bases and silently
drop its 10 kN. See :mod:`ada.cadit.cae.analysis` for the whole picture.
"""

from __future__ import annotations

import pathlib

import pytest

import ada
from ada.cadit.cae import (
    CARRIED_ELEMENT_TYPES,
    REFUSED_LOAD_TYPES,
    AnalysisPlan,
    CaeWriteError,
    build_plan,
)
from ada.fem import Bc, FemSet, Load, LoadGravity, StepEigen, StepImplicitStatic
from ada.materials.metals import CarbonSteel

from .cae_script_graph import CaeGraphError, ScriptGraph, check_emitted_script
from .conftest import require_writer

require_writer()

HEIGHT = 6.0
SPAN = 8.0
MESH_SIZE = 1.0
#: 10 kN split between the two top corners, which is what makes the frame's response exactly
#: antisymmetric -- the same reasoning as ``verification.genie_vs_abaqus.model``.
P_TOTAL = 10.0e3


def nset(fem, name: str, *points) -> FemSet:
    """A node set at the given positions, refusing anything but one node at each."""
    nodes = []
    for point in points:
        found = fem.nodes.get_by_volume(p=point, tol=1e-06)
        assert len(found) == 1, "expected one node at {}, found {}".format(point, len(found))
        nodes += list(found)
    return fem.add_set(FemSet(name, nodes, FemSet.TYPES.NSET, parent=fem))


def portal_frame(mesh_size: float = MESH_SIZE) -> ada.Assembly:
    """Two columns, a girder, both bases fixed, a horizontal load at each top corner.

    The structure the cross-solver comparison is derived from, rebuilt here rather than imported
    from ``verification/``: a unit test that needed a verification package on the path would be
    a unit test with a licence-shaped dependency.
    """
    mat = ada.Material("S355", CarbonSteel("S355"))
    part = ada.Part("PortalFrame")
    part / (
        ada.Beam("COL_L", (0, 0, 0), (0, 0, HEIGHT), "OD200x10", mat),
        ada.Beam("COL_R", (SPAN, 0, 0), (SPAN, 0, HEIGHT), "OD200x10", mat),
        ada.Beam("GIRDER", (0, 0, HEIGHT), (SPAN, 0, HEIGHT), "OD200x10", mat),
    )
    part.fem = part.to_fem_obj(mesh_size, "line")

    base_l = nset(part.fem, "BASE_L", (0.0, 0.0, 0.0))
    base_r = nset(part.fem, "BASE_R", (SPAN, 0.0, 0.0))
    top_l = nset(part.fem, "TOP_L", (0.0, 0.0, HEIGHT))
    top_r = nset(part.fem, "TOP_R", (SPAN, 0.0, HEIGHT))
    part.fem.add_bc(Bc("FIX_L", base_l, [1, 2, 3, 4, 5, 6]))
    part.fem.add_bc(Bc("FIX_R", base_r, [1, 2, 3, 4, 5, 6]))

    assembly = ada.Assembly("PortalSite") / part
    step = assembly.fem.add_step(StepImplicitStatic("static", nl_geom=False, total_time=1, init_incr=1, max_incr=1))
    step.add_load(Load("PX_L", Load.TYPES.FORCE, P_TOTAL / 2, fem_set=top_l, dof=[1, 0, 0, 0, 0, 0]))
    step.add_load(Load("PX_R", Load.TYPES.FORCE, P_TOTAL / 2, fem_set=top_r, dof=[1, 0, 0, 0, 0, 0]))
    return assembly


def bare_frame() -> ada.Assembly:
    """The same frame, meshed, with no support, load or step at all."""
    mat = ada.Material("S355", CarbonSteel("S355"))
    part = ada.Part("PortalFrame")
    part / (
        ada.Beam("COL_L", (0, 0, 0), (0, 0, HEIGHT), "OD200x10", mat),
        ada.Beam("GIRDER", (0, 0, HEIGHT), (SPAN, 0, HEIGHT), "OD200x10", mat),
    )
    part.fem = part.to_fem_obj(MESH_SIZE, "line")
    return ada.Assembly("PortalSite") / part


def emit(root: ada.Part, tmp_path: pathlib.Path, name: str = "portal", **kwargs) -> tuple[pathlib.Path, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    destination = tmp_path / (name + ".py")
    root.to_abaqus_cae_script(destination, **kwargs)
    return destination, destination.read_text(encoding="utf-8")


# ------------------------------------------------------------------ where adapy keeps them


def test_the_frames_supports_are_on_the_part_and_its_loads_on_the_assembly():
    """The measurement the whole gather is built on. Pinned so adapy cannot move it silently.

    ``Bc`` records live on ``FEM.bcs``; a ``Load`` lives inside a ``Step``, and the step is on a
    *different* FEM from the supports. Both stores are FEM-level -- ``Part.concept_fem``, adapy's
    other store for exactly these concepts, is empty here and is what the GeniE reader fills.
    """
    assembly = portal_frame()
    part = assembly.get_by_name("PortalFrame")

    assert [bc.name for bc in part.fem.bcs] == ["FIX_L", "FIX_R"]
    assert list(assembly.fem.bcs) == [], "the supports are on the part's FEM, not the assembly's"
    assert [step.name for step in part.fem.steps] == [], "the step is not on the part's FEM"
    assert [step.name for step in assembly.fem.steps] == ["static"]
    assert [load.name for load in assembly.fem.steps[0].loads] == ["PX_L", "PX_R"]
    # The other store, and it is genuinely a different one -- see ada.cadit.cae.analysis.
    assert part.concept_fem.constraints.point_constraints == {}
    assert part.concept_fem.loads.load_cases == {}


def test_writing_only_the_part_would_lose_the_step_so_it_is_gathered_assembly_wide():
    """Both routes reach the same analysis, because a step is an assembly-wide object.

    Writing the *part* must not produce a model with supports and no load. The gather walks the
    assembly, so it does not.
    """
    assembly = portal_frame()
    part = assembly.get_by_name("PortalFrame")

    from_assembly = build_plan(assembly).analysis
    from_part = build_plan(part).analysis

    assert [step.cae_name for step in from_part.steps] == ["static"]
    assert [load.load_name for load in from_part.loads] == ["PX_L", "PX_R"]
    assert [step.cae_name for step in from_assembly.steps] == [step.cae_name for step in from_part.steps]
    assert from_assembly.applied_resultant() == from_part.applied_resultant()


# ----------------------------------------------------------------------- what is carried


def test_a_model_with_no_analysis_emits_none_of_it(tmp_path):
    """A geometry-only model reads exactly as it did before any of this existed."""
    _, source = emit(bare_frame(), tmp_path, name="bare")

    graph = ScriptGraph(source, name="bare.py")
    assert graph.by_method("StaticStep", "DisplacementBC", "ConcentratedForce", "Moment") == []
    assert [c for c in graph.calls if c.method == "_analysis_region"] == []
    for absent in ("PLANNED_ANALYSIS", "APPLIED_FORCE", "_guard_analysis", "JOB_NAME"):
        assert absent not in source, "a model with no analysis should not carry {}".format(absent)
    assert build_plan(bare_frame()).analysis.is_empty


def test_a_fixed_support_becomes_a_displacement_bc_holding_all_six_dofs_at_zero(tmp_path):
    _, source = emit(portal_frame(), tmp_path)

    graph = check_emitted_script(source, name="portal.py")
    bcs = {call.kw("name"): call for call in graph.by_method("DisplacementBC")}
    assert sorted(bcs) == ["FIX_L", "FIX_R"]
    for name, call in bcs.items():
        assert call.kw("createStepName") == "Initial", "a FEM-level support applies from the start"
        for keyword in ("u1", "u2", "u3", "ur1", "ur2", "ur3"):
            assert call.kw(keyword) == 0.0, "{} left {} free".format(name, keyword)
    regions = {call.args[1] for call in graph.calls if call.method == "_analysis_region"}
    assert {"BASE_L", "BASE_R", "TOP_L", "TOP_R"} == regions


def test_a_prescribed_support_magnitude_is_written_as_the_displacement_it_is(tmp_path):
    """The gap on the Sesam side, deliberately not reproduced.

    ``write_bcs`` there defines ``PRESCRIBED = 2`` and its own comment says the magnitudes "are
    not carried into BNDISPL yet", so a settlement case silently becomes a fixed support.
    Abaqus expresses it natively, so this writer writes it -- and says so in the comment above
    the call, because a reader of the script has to be able to tell the two apart.
    """
    assembly = portal_frame()
    part = assembly.get_by_name("PortalFrame")
    settle = nset(part.fem, "SETTLE", (SPAN, 0.0, 0.0))
    part.fem.add_bc(Bc("SETTLEMENT", settle, [3], magnitudes=[-0.012]))

    _, source = emit(assembly, tmp_path, name="settle")

    graph = check_emitted_script(source, name="settle.py")
    bcs = {c.kw("name"): c for c in graph.by_method("DisplacementBC")}
    call = bcs["SETTLEMENT"]
    assert call.kw("u3") == pytest.approx(-0.012), "the magnitude adapy holds, unrounded"
    assert call.raw_kwargs["u1"] == "UNSET", "a DOF the record does not name stays free"
    assert call.raw_kwargs["ur2"] == "UNSET"
    assert "prescribed displacement" in source
    # Measured, not a preference: Abaqus refuses a non-zero support in the initial step outright
    # ("Non-zero boundary condition in initial step."), so a prescribed one goes to the first
    # analysis step while the fixed bases stay in 'Initial'.
    assert call.kw("createStepName") == "static"
    assert bcs["FIX_L"].kw("createStepName") == "Initial"


def test_a_prescribed_support_with_no_step_to_apply_it_in_is_refused(tmp_path):
    """Nowhere for it to go: Abaqus will not take a non-zero support in the initial step."""
    assembly = portal_frame()
    part = assembly.get_by_name("PortalFrame")
    assembly.fem.steps.clear()
    settle = nset(part.fem, "SETTLE", (SPAN, 0.0, 0.0))
    part.fem.add_bc(Bc("SETTLEMENT", settle, [3], magnitudes=[-0.012]))

    with pytest.raises(CaeWriteError, match="Non-zero boundary condition in initial step"):
        assembly.to_abaqus_cae_script(tmp_path / "refused.py")


def test_a_force_load_becomes_a_concentrated_force_and_a_moment_under_the_inp_writers_names(tmp_path):
    """One adapy ``Load`` is one ``<name>_F`` and one ``<name>_M`` -- as in the INP writer.

    ``force_load_str`` splits a ``Load`` into a ``*Cload`` block named ``<name>_F`` for DOFs 1-3
    and one named ``<name>_M`` for 4-6, off the same ``Load.forces``. Matching that means a name
    in an Abaqus result traces back to one adapy load whichever route wrote the deck.
    """
    assembly = portal_frame()
    top_l = assembly.get_by_name("PortalFrame").fem.sets.get_nset_from_name("TOP_L")
    assembly.fem.steps[0].add_load(Load("TWIST", Load.TYPES.FORCE, 250.0, fem_set=top_l, dof=[0, 0, 0, 0, 2, 0]))

    _, source = emit(assembly, tmp_path, name="loads")

    graph = check_emitted_script(source, name="loads.py")
    forces = {call.kw("name"): call for call in graph.by_method("ConcentratedForce")}
    moments = {call.kw("name"): call for call in graph.by_method("Moment")}
    assert sorted(forces) == ["PX_L_F", "PX_R_F"]
    assert sorted(moments) == ["TWIST_M"], "a moment-only load emits no zero ConcentratedForce"
    assert forces["PX_L_F"].kw("cf1") == pytest.approx(P_TOTAL / 2)
    assert forces["PX_L_F"].kw("cf2") == 0.0
    assert moments["TWIST_M"].kw("cm2") == pytest.approx(500.0), "the dof entry times the magnitude"
    for call in list(forces.values()) + list(moments.values()):
        assert call.kw("createStepName") == "static"


def test_the_applied_resultant_counts_every_node_a_load_acts_on():
    """Abaqus applies a ConcentratedForce's full component to EVERY node of its region.

    That is ``*Cload`` over a node set too, so the two Abaqus routes agree -- and it is the
    opposite of adapy's Sesam writer, whose ``load_force`` reads ``fem_set.members[0]`` and
    ignores the rest. The number the emitted script checks the solver's reaction total against
    therefore has to count the vertices, or a two-node load would look like a failed solve.
    """
    assembly = portal_frame()
    part = assembly.get_by_name("PortalFrame")
    both = nset(part.fem, "BOTH_TOPS", (0.0, 0.0, HEIGHT), (SPAN, 0.0, HEIGHT))
    lonely = portal_frame()
    lonely.fem.steps[0].loads.clear()
    assembly.fem.steps[0].loads.clear()
    assembly.fem.steps[0].add_load(Load("PAIR", Load.TYPES.FORCE, 1000.0, fem_set=both, dof=[1, 0, 0, 0, 0, 0]))

    plan = build_plan(assembly).analysis

    assert plan.loads[0].node_count == 2
    assert plan.applied_resultant() == (2000.0, 0.0, 0.0), "1 kN at each of the two nodes"


def test_every_step_is_written_and_chained(tmp_path):
    """adapy's Sesam writer emits only the first step of a multi-step model. This writes them all."""
    assembly = portal_frame()
    second = assembly.fem.add_step(StepImplicitStatic("pull", nl_geom=False, total_time=2, init_incr=2, max_incr=2))
    top_r = assembly.get_by_name("PortalFrame").fem.sets.get_nset_from_name("TOP_R")
    second.add_load(Load("PZ", Load.TYPES.FORCE, -3000.0, fem_set=top_r, dof=[0, 0, 1, 0, 0, 0]))

    _, source = emit(assembly, tmp_path, name="twostep")

    graph = check_emitted_script(source, name="twostep.py")
    steps = graph.by_method("StaticStep")
    assert [call.kw("name") for call in steps] == ["static", "pull"]
    assert [call.kw("previous") for call in steps] == ["Initial", "static"]
    assert steps[1].kw("timePeriod") == pytest.approx(2.0)
    assert len(graph.by_method("FieldOutputRequest")) == 1, "one request, on the first step, applies onwards"


def test_the_nlgeom_flag_follows_the_step(tmp_path):
    assembly = portal_frame()
    assembly.fem.steps.clear()
    step = assembly.fem.add_step(StepImplicitStatic("big", nl_geom=True, total_time=1, init_incr=1, max_incr=1))
    top_l = assembly.get_by_name("PortalFrame").fem.sets.get_nset_from_name("TOP_L")
    step.add_load(Load("PX", Load.TYPES.FORCE, 5000.0, fem_set=top_l, dof=[1, 0, 0, 0, 0, 0]))

    _, source = emit(assembly, tmp_path, name="nlgeom")

    assert "nlgeom=ON)" in source
    linear = emit(portal_frame(), tmp_path, name="linear")[1]
    assert "nlgeom=OFF)" in linear


# ------------------------------------------------------------------------- mesh and solve


def test_the_mesh_block_seeds_types_and_meshes_each_part(tmp_path):
    _, source = emit(portal_frame(), tmp_path, name="meshed", mesh_size=MESH_SIZE, element_type="B32")

    graph = check_emitted_script(source, name="meshed.py")
    calls = [c for c in graph.calls if c.method == "_mesh_part"]
    assert len(calls) == 1
    assert calls[0].args[0] == "PortalFrame"
    assert calls[0].args[2] == pytest.approx(MESH_SIZE)
    assert calls[0].raw_kwargs == {}
    assert "from mesh import ElemType" in source
    assert "elemCode=element_code" in source, "the element code is passed, not hardcoded in the helper"
    assert "_mesh_part('PortalFrame', part_0, 1.0, B32, 'B32', S4R, 'S4R')" in source


def test_no_mesh_is_emitted_without_a_mesh_size(tmp_path):
    """An analysis definition attaches to vertices, so it needs no mesh -- and does not get one."""
    _, source = emit(portal_frame(), tmp_path, name="unmeshed")

    assert "from mesh import ElemType" not in source
    assert "_mesh_part" not in source
    assert "seedPart" not in source
    # ... and the analysis is all there anyway.
    graph = check_emitted_script(source, name="unmeshed.py")
    assert len(graph.by_method("DisplacementBC")) == 2
    assert len(graph.by_method("ConcentratedForce")) == 2


def test_the_solve_block_is_emitted_only_on_submit(tmp_path):
    _, without = emit(portal_frame(), tmp_path, name="nosolve", mesh_size=MESH_SIZE)
    _, with_solve = emit(portal_frame(), tmp_path, name="solve", mesh_size=MESH_SIZE, submit=True)

    assert "mdb.Job(" not in without
    assert "JOB_NAME" not in without
    assert "mdb.Job(name=JOB_NAME, model=MODEL_NAME)" in with_solve
    assert "job.waitForCompletion()" in with_solve
    assert "solve.cae_displacements.json" in with_solve
    code_lines = [line for line in with_solve.splitlines() if not line.lstrip().startswith("#")]
    assert not [line for line in code_lines if "job.status" in line], (
        "job.status reads None after waitForCompletion under 'cae noGUI=' (measured), so no CODE may "
        "branch on it -- the comments that say so are welcome"
    )
    assert "THE ANALYSIS HAS COMPLETED SUCCESSFULLY" in with_solve
    check_emitted_script(with_solve, name="solve.py")


def test_the_job_is_named_after_the_script_unless_told_otherwise(tmp_path):
    _, default = emit(portal_frame(), tmp_path, name="whatever", mesh_size=MESH_SIZE, submit=True)
    _, named = emit(portal_frame(), tmp_path, name="whatever2", mesh_size=MESH_SIZE, submit=True, job_name="lc1")

    assert "JOB_NAME = 'whatever'" in default
    assert "JOB_NAME = 'lc1'" in named


def test_the_equilibrium_check_carries_the_resultant_adapy_computed(tmp_path):
    """The load that arrived, in newtons, against a number from the adapy side.

    The ranked failure list in the comparison harness put "the full 10 kN must arrive" third,
    and said to read it out of the ``.dat`` by hand. This is that check, in the script.
    """
    _, source = emit(portal_frame(), tmp_path, name="equil", mesh_size=MESH_SIZE, submit=True)

    assert "APPLIED_FORCE = (10000.0, 0.0, 0.0)" in source
    assert "_guard_equilibrium" in source
    assert "_sum_nodal_field(frame, 'RF')" in source
    assert "_sum_nodal_field(frame, 'CF')" in source


# ------------------------------------------------------------------------------ refusals


def with_bc(bc: Bc, *, at=(0.0, 0.0, 0.0), set_name: str = "SUPPORT") -> ada.Assembly:
    """The frame with one extra ``Bc``, built from a factory so the failure is the test's."""
    assembly = portal_frame()
    part = assembly.get_by_name("PortalFrame")
    fem_set = nset(part.fem, set_name, at)
    bc._fem_set = fem_set
    fem_set.refs.append(bc)
    part.fem.add_bc(bc)
    return assembly


def refuse_gravity() -> ada.Assembly:
    assembly = portal_frame()
    assembly.fem.steps[0].add_load(LoadGravity("gravity"))
    return assembly


def refuse_pressure() -> ada.Assembly:
    from ada.fem import Surface

    assembly = portal_frame()
    part = assembly.get_by_name("PortalFrame")
    top_l = part.fem.sets.get_nset_from_name("TOP_L")
    from ada.fem.loads import LoadPressure

    assembly.fem.steps[0].add_load(LoadPressure("p", 1.0e5, Surface("s", Surface.TYPES.NODE, top_l, parent=part.fem)))
    return assembly


def refuse_eigen_step() -> ada.Assembly:
    assembly = portal_frame()
    assembly.fem.steps.clear()
    assembly.fem.add_step(StepEigen("modes", num_eigen_modes=5))
    return assembly


def refuse_velocity_bc() -> ada.Assembly:
    return with_bc(Bc("MOVING", _placeholder_set(), [1], bc_type=Bc.TYPES.VELOCITY))


def refuse_mid_span_support() -> ada.Assembly:
    """A support at a mid-span *mesh node*, where the geometry has no vertex."""
    return with_bc(Bc("MIDSPAN", _placeholder_set(), [1, 2, 3]), at=(0.0, 0.0, HEIGHT / 2), set_name="MID")


def refuse_empty_dofs() -> ada.Assembly:
    return with_bc(Bc("NOTHING", _placeholder_set(), [None, None, None, None, None, None]))


def refuse_region_name_clash() -> ada.Assembly:
    """A support region named after a member, which would be two CAE sets called ``COL_L``."""
    return with_bc(Bc("CLASH", _placeholder_set(), [1, 2, 3]), set_name="COL_L")


def refuse_load_in_a_local_csys() -> ada.Assembly:
    from ada.fem import Csys

    assembly = portal_frame()
    top_l = assembly.get_by_name("PortalFrame").fem.sets.get_nset_from_name("TOP_L")
    assembly.fem.steps[0].add_load(
        Load("SKEW", Load.TYPES.FORCE, 1000.0, fem_set=top_l, dof=[1, 0, 0, 0, 0, 0], csys=Csys("local"))
    )
    return assembly


def refuse_zero_load() -> ada.Assembly:
    assembly = portal_frame()
    top_l = assembly.get_by_name("PortalFrame").fem.sets.get_nset_from_name("TOP_L")
    assembly.fem.steps[0].add_load(Load("NOTHING", Load.TYPES.FORCE, 0.0, fem_set=top_l, dof=[1, 0, 0, 0, 0, 0]))
    return assembly


def refuse_concept_constraint() -> ada.Assembly:
    assembly = portal_frame()
    part = assembly.get_by_name("PortalFrame")
    part.concept_fem.constraints.add_point_constraint(
        ada.ConstraintConceptPoint("genie_support", (0, 0, 0), ada.ConstraintConceptDofType.encastre())
    )
    return assembly


def refuse_concept_load_case() -> ada.Assembly:
    from ada.fem.concept.loads import LoadConceptCase, LoadConceptPoint

    assembly = portal_frame()
    part = assembly.get_by_name("PortalFrame")
    part.concept_fem.loads.add_load_case(
        LoadConceptCase("LC_genie", [LoadConceptPoint("p", (0, 0, HEIGHT), (1000.0, 0.0, 0.0), (0.0, 0.0, 0.0))])
    )
    return assembly


def refuse_steps_on_two_fems() -> ada.Assembly:
    assembly = portal_frame()
    part = assembly.get_by_name("PortalFrame")
    part.fem.add_step(StepImplicitStatic("other", nl_geom=False, total_time=1, init_incr=1, max_incr=1))
    return assembly


def _placeholder_set() -> FemSet:
    """A throwaway set, replaced by :func:`with_bc`. ``Bc.__init__`` insists on having one."""
    return FemSet("placeholder", [], FemSet.TYPES.NSET)


#: Each entry is a factory, built inside the test: a model constructed at import time would turn
#: a broken fixture into a collection error for the whole session.
REFUSALS = {
    # The expected text is a phrase unique to that type's entry in REFUSED_LOAD_TYPES, not the type
    # name: falling through to the "a Load type this writer has never seen" refusal would still name
    # the type and would still raise, and it is not the same thing. A refusal that cannot say why is
    # barely a refusal.
    "a gravity load": (refuse_gravity, "density it multiplies"),
    "a pressure load": (refuse_pressure, "no face for it to act on"),
    "an eigenvalue step": (refuse_eigen_step, "StepEigen"),
    "a velocity boundary condition": (refuse_velocity_bc, "prescribes a rate"),
    "a support at a mid-span mesh node": (refuse_mid_span_support, "no vertex there"),
    "a support that restrains nothing": (refuse_empty_dofs, "restrains no degree of freedom"),
    "a region named after a member": (refuse_region_name_clash, "already claims it for a part-level set"),
    "a load in a local csys": (refuse_load_in_a_local_csys, "local coordinate system"),
    "a load of zero": (refuse_zero_load, "every component zero"),
    "a concept_fem point constraint": (refuse_concept_constraint, "Part.concept_fem"),
    "a concept_fem load case": (refuse_concept_load_case, "Part.concept_fem"),
    "steps on two FEMs": (refuse_steps_on_two_fems, "nothing says what order they run in"),
}


@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_the_writer_refuses_an_analysis_it_cannot_carry_faithfully(case, tmp_path):
    """Each of these would otherwise be a model that opens, solves, and answers the wrong question.

    The message is checked, not only the exception: adapy's Sesam writer *does* fail on an
    unsupported load type, and it fails as ``TypeError: can only concatenate str (not
    "NoneType") to str`` several frames from the cause, although ``ada.fem.exceptions`` has
    defined ``UnsupportedLoadType`` all along. A refusal nobody can act on is barely a refusal.
    """
    factory, expected = REFUSALS[case]
    assembly = factory()

    with pytest.raises(CaeWriteError, match=expected):
        assembly.to_abaqus_cae_script(tmp_path / "refused.py")


def test_every_refused_load_type_is_named_with_a_reason():
    """The table, not a fallthrough: a type with no entry would be refused as unknown instead."""
    from ada.fem.loads.fe_loads import LoadTypes

    carried = {"force"}
    assert set(REFUSED_LOAD_TYPES) | carried >= set(
        LoadTypes.all
    ), "a Load type exists that is neither carried nor named in REFUSED_LOAD_TYPES: " "{}".format(
        sorted(set(LoadTypes.all) - set(REFUSED_LOAD_TYPES) - carried)
    )
    for load_type, reason in REFUSED_LOAD_TYPES.items():
        assert len(reason) > 40, "{} is refused without saying why".format(load_type)


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"element_type": "B21"}, "element_type='B21' is refused"),
        ({"element_type": "T3D2"}, "element_type='T3D2' is refused"),
        ({"submit": True}, "submit=True with no mesh_size"),
        ({"mesh_size": 0.0}, "mesh_size=0.0 is not a length"),
        ({"mesh_size": -1.0}, "is not a length"),
        ({"mesh_size": 1.0, "job_name": "job.1"}, "cannot be used"),
        ({"mesh_size": 1.0, "job_name": "my job"}, "cannot be used"),
    ],
)
def test_the_mesh_and_job_arguments_are_refused_rather_than_coerced(kwargs, expected, tmp_path):
    with pytest.raises(CaeWriteError, match=re_escape(expected)):
        portal_frame().to_abaqus_cae_script(tmp_path / "refused.py", **kwargs)


def re_escape(text: str) -> str:
    import re

    return re.escape(text)


def test_an_element_type_is_validated_even_when_nothing_is_meshed(tmp_path):
    """Accepting it because this call happens not to reach the mesher is how it gets trusted."""
    with pytest.raises(CaeWriteError, match="element_type='B99' is refused"):
        portal_frame().to_abaqus_cae_script(tmp_path / "refused.py", element_type="B99")

    assert CARRIED_ELEMENT_TYPES == ("B31", "B32", "B33")


# ------------------------------------------------------------------------- the graph pass


def test_the_analysis_graph_check_has_teeth_on_a_step_that_does_not_exist(tmp_path):
    _, source = emit(portal_frame(), tmp_path, name="teeth")
    check_emitted_script(source, name="teeth.py")

    mutated = source.replace("createStepName='static'", "createStepName='nowhere'", 1)
    assert mutated != source

    with pytest.raises(CaeGraphError, match="neither 'Initial' nor a step this script creates"):
        check_emitted_script(mutated, name="teeth.py")


def test_the_analysis_graph_check_has_teeth_on_a_region_that_is_never_built(tmp_path):
    _, source = emit(portal_frame(), tmp_path, name="teeth2")

    mutated = source.replace("assembly.sets['TOP_L']", "assembly.sets['TOP_LEFT']", 1)
    assert mutated != source

    with pytest.raises(CaeGraphError, match="which is never created"):
        check_emitted_script(mutated, name="teeth2.py")


def test_the_analysis_graph_check_has_teeth_on_a_support_that_restrains_nothing(tmp_path):
    _, source = emit(portal_frame(), tmp_path, name="teeth3")

    anchor = "u1=0.0, u2=0.0, u3=0.0, ur1=0.0, ur2=0.0, ur3=0.0"
    assert source.count(anchor) == 2
    mutated = source.replace(anchor, "u1=UNSET, u2=UNSET, u3=UNSET, ur1=UNSET, ur2=UNSET, ur3=UNSET", 1)

    with pytest.raises(CaeGraphError, match="leaves every DOF UNSET"):
        check_emitted_script(mutated, name="teeth3.py")


def test_the_analysis_graph_check_has_teeth_on_a_region_nothing_acts_on(tmp_path):
    """An orphan region is a support or a load that did not arrive."""
    _, source = emit(portal_frame(), tmp_path, name="teeth4")

    lines = [line for line in source.splitlines() if "model.ConcentratedForce(name='PX_R_F'" in line]
    assert len(lines) == 1
    mutated = "\n".join(
        line for line in source.splitlines() if "PX_R_F" not in line and "assembly.sets['TOP_R']" not in line
    )

    with pytest.raises(CaeGraphError, match="no support or load acts on them"):
        check_emitted_script(mutated, name="teeth4.py")


def test_the_plan_is_empty_for_a_model_with_no_analysis():
    assert AnalysisPlan().is_empty
    assert AnalysisPlan().applied_resultant() == (0.0, 0.0, 0.0)
