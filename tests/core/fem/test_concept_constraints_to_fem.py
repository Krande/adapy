import pytest

import ada
from ada.api.transforms import to_global_points, to_global_vectors
from ada.base.types import GeomRepr


def _beam() -> ada.Beam:
    return ada.Beam("bm1", (0, 0, 0), (1, 0, 0), "IPE300")


def _section_nodes(fem: ada.FEM, name: str) -> list[ada.Node]:
    """The nodes a support acts on: the coupled section of a shell/solid beam end, else the nodes of the Bc"""
    coupling = fem.constraints.get(f"{name}_cpl")
    if coupling is not None:
        return coupling.s_set.members
    return {bc.name: bc for bc in fem.bcs}[name].fem_set.members


def _cantilever(bm_repr: GeomRepr, dofs) -> ada.FEM:
    bm = _beam()
    p = ada.Part("P1") / bm
    p.concept_fem.constraints.add_point_constraint(ada.ConstraintConceptPoint("bc1", bm.n1.p, dofs))
    return p.to_fem_obj(0.1, bm_repr)


def test_point_constraint_on_line_beam():
    fem = _cantilever(GeomRepr.LINE, ada.ConstraintConceptDofType.encastre())

    assert len(fem.bcs) == 1
    bc = fem.bcs[0]
    assert bc.dofs == [1, 2, 3, 4, 5, 6]
    assert len(bc.fem_set.members) == 1
    assert bc.fem_set.members[0].p.is_equal(ada.Point(0, 0, 0))


def test_pinned_point_constraint_skips_free_dofs():
    fem = _cantilever(GeomRepr.LINE, ada.ConstraintConceptDofType.pinned())

    assert fem.bcs[0].dofs == [1, 2, 3]


@pytest.mark.parametrize("dofs", ["encastre", "pinned"])
@pytest.mark.parametrize("bm_repr", [GeomRepr.SHELL, GeomRepr.SOLID])
def test_point_constraint_on_beam_end_face_acts_through_a_coupled_reference_node(bm_repr, dofs):
    fem = _cantilever(bm_repr, getattr(ada.ConstraintConceptDofType, dofs)())

    # the support acts on a reference node of its own at the beam end, with rotational dofs also for solids
    assert len(fem.bcs) == 1
    bc = fem.bcs[0]
    assert len(bc.fem_set.members) == 1
    ref = bc.fem_set.members[0]
    assert ref.p.is_equal(ada.Point(0, 0, 0))
    assert bc.dofs == ([1, 2, 3, 4, 5, 6] if dofs == "encastre" else [1, 2, 3])

    # the section follows the reference node as a rigid body
    coupling = fem.constraints["bc1_cpl"]
    assert coupling.type == coupling.TYPES.COUPLING
    assert coupling.m_set.members == [ref]
    assert coupling.dofs == [1, 2, 3, 4, 5, 6]
    section = coupling.s_set.members
    assert len(section) > 1
    assert ref not in section
    assert all(abs(n.x) < 1e-4 for n in section)


@pytest.mark.parametrize("bm_repr, dofs", [(GeomRepr.SHELL, [1, 2, 3, 4, 5, 6]), (GeomRepr.SOLID, [1, 2, 3])])
def test_direct_section_support_restrains_the_section_nodes_themselves(bm_repr, dofs):
    """``section_support="direct"``: no reference node and no coupling for the solver to support. Solid-only nodes
    have no rotations, so they get the translations alone."""
    bm = _beam()
    p = ada.Part("P1") / bm
    bm.concept_fem.fix_end("n1", section_support="direct")
    fem = bm.to_fem_obj(0.1, bm_repr)

    assert len(fem.constraints) == 0
    (bc,) = fem.bcs
    assert bc.dofs == dofs
    section = bc.fem_set.members
    assert len(section) > 1
    assert all(abs(n.x) < 1e-4 for n in section)
    assert p is not None


def test_coupled_section_support_is_the_default():
    bm = _beam()
    assert bm.concept_fem.fix_end("n1").section_support == "coupled"


def test_an_unknown_section_support_is_refused():
    with pytest.raises(ValueError, match="section support"):
        _beam().concept_fem.fix_end("n1", section_support="glued")


def test_beam_end_convenience_api():
    bm = _beam()
    fixed = bm.concept_fem.fix_end("n1")
    pinned = bm.concept_fem.pin_end("n2", name="pin")

    assert isinstance(fixed, ada.ConstraintConceptBeamEnd)
    assert fixed.name == "bm1_n1"
    assert fixed.position.is_equal(ada.Point(0, 0, 0))
    assert pinned.position.is_equal(ada.Point(1, 0, 0))
    assert {d.dof for d in pinned.dof_constraints if d.constraint_type == "fixed"} == {"dx", "dy", "dz"}


def test_beam_end_must_be_n1_or_n2():
    with pytest.raises(ValueError, match="Invalid beam end"):
        _beam().concept_fem.fix_end("n3")


def test_beam_end_name_must_be_unique():
    bm = _beam()
    bm.concept_fem.fix_end("n1")
    with pytest.raises(ValueError, match="already exists"):
        bm.concept_fem.pin_end("n1")


def test_beam_without_concepts_gets_no_concept_container():
    bm = _beam()
    ada.Part("P1") / bm
    bm.to_fem_obj(0.1, GeomRepr.LINE)

    assert bm.has_concept_fem is False


@pytest.mark.parametrize("in_part", [True, False])
@pytest.mark.parametrize("bm_repr", [GeomRepr.LINE, GeomRepr.SHELL, GeomRepr.SOLID])
def test_beam_to_fem_obj_applies_beam_end_constraints(bm_repr, in_part):
    bm = _beam()
    if in_part:
        ada.Part("P1") / bm
    bm.concept_fem.fix_end("n1")
    bm.concept_fem.pin_end("n2")

    fem = bm.to_fem_obj(0.1, bm_repr)

    bcs = {bc.name: bc for bc in fem.bcs}
    assert set(bcs) == {"bm1_n1", "bm1_n2"}
    assert all(abs(n.x) < 1e-4 for n in _section_nodes(fem, "bm1_n1"))
    assert all(abs(n.x - 1) < 1e-4 for n in _section_nodes(fem, "bm1_n2"))
    assert bcs["bm1_n2"].dofs == [1, 2, 3]
    if bm_repr == GeomRepr.LINE:
        assert len(fem.constraints) == 0
    else:
        assert set(fem.constraints) == {"bm1_n1_cpl", "bm1_n2_cpl"}
        assert len(_section_nodes(fem, "bm1_n1")) > 1


def test_part_to_fem_obj_applies_beam_end_constraints():
    bm = _beam()
    p = ada.Part("P1") / bm
    bm.concept_fem.fix_end("n2")

    fem = p.to_fem_obj(0.1, GeomRepr.SHELL)

    assert [bc.name for bc in fem.bcs] == ["bm1_n2"]
    assert all(abs(n.x - 1) < 1e-4 for n in _section_nodes(fem, "bm1_n2"))


def _overlap_finding(report, name: str):
    """The conversion report's finding that support ``name`` gave way to a beam end support."""
    (finding,) = [
        f
        for f in report.findings
        if f.stage == "concept to fem" and name in [f.subject, *f.other_subjects] and "beam end" in f.reason
    ]
    return finding


def test_beam_end_constraint_takes_priority_over_point_constraint():
    from ada.fem.formats import conversion_report

    bm = _beam()
    p = ada.Part("P1") / bm
    bm.concept_fem.pin_end("n1")
    p.concept_fem.constraints.add_point_constraint(
        ada.ConstraintConceptPoint("bc1", bm.n1.p, ada.ConstraintConceptDofType.encastre())
    )

    with conversion_report.collect() as report:
        fem = p.to_fem_obj(0.1, GeomRepr.SHELL)

    assert [bc.name for bc in fem.bcs] == ["bm1_n1"]
    assert fem.bcs[0].dofs == [1, 2, 3]
    assert _overlap_finding(report, "bc1").details["beam_end_supports"] == ["bm1_n1"]


@pytest.mark.parametrize(
    "placement",
    [ada.Placement(origin=(10, 0, 0)), ada.Placement(origin=(10, 0, 0), xdir=(0, 1, 0), zdir=(0, 0, 1))],
    ids=["translated", "rotated"],
)
@pytest.mark.parametrize("bm_repr", [GeomRepr.LINE, GeomRepr.SHELL, GeomRepr.SOLID])
def test_constraints_follow_part_placement(bm_repr, placement):
    bm = _beam()
    p = ada.Part("P1", placement=placement) / bm
    ada.Assembly() / p
    bm.concept_fem.fix_end("n1")
    p.concept_fem.constraints.add_point_constraint(
        ada.ConstraintConceptPoint("bc2", bm.n2.p, ada.ConstraintConceptDofType.pinned())
    )

    fem = p.to_fem_obj(0.1, bm_repr)

    xvec = to_global_vectors(bm, bm.xvec)
    p1, p2 = to_global_points(bm, [bm.n1.p, bm.n2.p])
    assert all(abs((n.p - p1) @ xvec) < 1e-4 for n in _section_nodes(fem, "bm1_n1"))
    assert all(abs((n.p - p2) @ xvec) < 1e-4 for n in _section_nodes(fem, "bc2"))
    if bm_repr != GeomRepr.LINE:
        assert len(_section_nodes(fem, "bm1_n1")) > 1
        assert len(_section_nodes(fem, "bc2")) > 1


def test_part_to_fem_obj_ignores_constraints_of_sibling_parts():
    bm1 = _beam()
    bm2 = ada.Beam("bm2", (0, 2, 0), (1, 2, 0), "IPE300")
    p1 = ada.Part("P1") / bm1
    p2 = ada.Part("P2") / bm2
    ada.Assembly() / [p1, p2]
    bm2.concept_fem.fix_end("n1")

    assert len(p1.to_fem_obj(0.1, GeomRepr.LINE).bcs) == 0


def test_global_constraint_concepts_include_beam_ends():
    bm = _beam()
    p = ada.Part("P1") / bm
    a = ada.Assembly() / p
    bm.concept_fem.fix_end("n1")

    concepts = a.concept_fem.constraints.get_global_constraint_concepts()

    assert list(concepts.beam_end_constraints) == ["bm1_n1"]
    assert concepts.beam_end_constraints["bm1_n1"].parent.parent_fem.parent_part is p


def _plate_part() -> ada.Part:
    pl = ada.Plate("pl1", [(0, 0), (2, 0), (2, 1), (0, 1)], 0.01)
    return ada.Part("P1") / pl


def test_curve_constraint_restrains_the_nodes_on_its_segment():
    p = _plate_part()
    p.concept_fem.constraints.add_curve_constraint(
        ada.ConstraintConceptCurve("edge", (0, 0, 0), (0, 1, 0), ada.ConstraintConceptDofType.pinned())
    )

    fem = p.to_fem_obj(0.25, pl_repr=GeomRepr.SHELL)

    bc = {bc.name: bc for bc in fem.bcs}["edge"]
    members = bc.fem_set.members
    assert len(members) >= 5  # 1 m edge at a 0.25 m seed
    assert all(abs(n.x) < 1e-4 for n in members)
    assert bc.dofs == [1, 2, 3]


def test_curve_constraint_yields_to_a_beam_end_constraint():
    from ada.fem.formats import conversion_report

    bm = _beam()
    p = ada.Part("P1") / bm
    bm.concept_fem.fix_end("n1")
    p.concept_fem.constraints.add_curve_constraint(
        ada.ConstraintConceptCurve("line", (0, 0, 0), (1, 0, 0), ada.ConstraintConceptDofType.pinned())
    )

    with conversion_report.collect() as report:
        fem = p.to_fem_obj(0.25, GeomRepr.LINE)

    bcs = {bc.name: bc for bc in fem.bcs}
    assert [n.x for n in bcs["bm1_n1"].fem_set.members] == [0.0]
    assert all(n.x > 0 for n in bcs["line"].fem_set.members)
    assert _overlap_finding(report, "line").details["beam_end_supports"] == ["bm1_n1"]


@pytest.mark.parametrize("rotation_dependent", [True, False])
def test_rigid_link_couples_the_region_to_a_supported_master_node(rotation_dependent):
    bm = _beam()
    p = ada.Part("P1") / bm
    region = ada.RigidLinkRegion((-0.01, -0.1, -0.2), (0.01, 0.1, 0.2))
    p.concept_fem.constraints.add_rigid_link(
        ada.ConstraintConceptRigidLink(
            "rl1", (-0.5, 0, 0), region, ada.ConstraintConceptDofType.encastre(), rotation_dependent=rotation_dependent
        )
    )

    fem = p.to_fem_obj(0.1, GeomRepr.SOLID)

    con = {c.name: c for c in fem.constraints.values()}["rl1"]
    assert con.type == con.TYPES.COUPLING
    master = con.m_set.members[0]
    assert master.p.is_equal(ada.Point(-0.5, 0, 0))
    dependents = con.s_set.members
    assert len(dependents) > 1
    assert all(abs(n.x) < 1e-4 for n in dependents)
    assert con.dofs == ([1, 2, 3, 4, 5, 6] if rotation_dependent else [1, 2, 3])

    support = {bc.name: bc for bc in fem.bcs}["rl1_support"]
    assert support.fem_set.members == [master]
    assert support.dofs == [1, 2, 3, 4, 5, 6]


def test_rigid_link_is_written_to_abaqus_as_a_coupling(tmp_path):
    bm = _beam()
    p = ada.Part("P1") / bm
    a = ada.Assembly("A") / p
    region = ada.RigidLinkRegion((-0.01, -0.1, -0.2), (0.01, 0.1, 0.2))
    p.concept_fem.constraints.add_rigid_link(
        ada.ConstraintConceptRigidLink("rl1", (-0.5, 0, 0), region, ada.ConstraintConceptDofType.encastre())
    )
    p.fem = p.to_fem_obj(0.1, GeomRepr.SOLID)
    a.fem.add_step(ada.fem.StepEigen("Eig", 5))

    a.to_fem("rl_deck", "abaqus", scratch_dir=tmp_path, overwrite=True, execute=False)

    deck = "\n".join(f.read_text().upper() for f in tmp_path.rglob("*.inp"))
    assert "*COUPLING" in deck
    assert "RL1_SUPPORT_SET" in deck
