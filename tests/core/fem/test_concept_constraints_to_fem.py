import logging

import pytest

import ada
from ada.api.transforms import to_global_points, to_global_vectors
from ada.base.types import GeomRepr


def _beam() -> ada.Beam:
    return ada.Beam("bm1", (0, 0, 0), (1, 0, 0), "IPE300")


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


@pytest.mark.parametrize("bm_repr", [GeomRepr.SHELL, GeomRepr.SOLID])
def test_point_constraint_on_beam_end_face(bm_repr):
    fem = _cantilever(bm_repr, ada.ConstraintConceptDofType.encastre())

    assert len(fem.bcs) == 1
    bc = fem.bcs[0]
    members = bc.fem_set.members
    assert len(members) > 1
    assert all(abs(n.x) < 1e-4 for n in members)
    assert bc.dofs == ([1, 2, 3] if bm_repr == GeomRepr.SOLID else [1, 2, 3, 4, 5, 6])


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
    assert all(abs(n.x) < 1e-4 for n in bcs["bm1_n1"].fem_set.members)
    assert all(abs(n.x - 1) < 1e-4 for n in bcs["bm1_n2"].fem_set.members)
    assert bcs["bm1_n2"].dofs == [1, 2, 3]
    if bm_repr != GeomRepr.LINE:
        assert len(bcs["bm1_n1"].fem_set.members) > 1


def test_part_to_fem_obj_applies_beam_end_constraints():
    bm = _beam()
    p = ada.Part("P1") / bm
    bm.concept_fem.fix_end("n2")

    fem = p.to_fem_obj(0.1, GeomRepr.SHELL)

    assert [bc.name for bc in fem.bcs] == ["bm1_n2"]
    assert all(abs(n.x - 1) < 1e-4 for n in fem.bcs[0].fem_set.members)


def test_beam_end_constraint_takes_priority_over_point_constraint(caplog):
    bm = _beam()
    p = ada.Part("P1") / bm
    bm.concept_fem.pin_end("n1")
    p.concept_fem.constraints.add_point_constraint(
        ada.ConstraintConceptPoint("bc1", bm.n1.p, ada.ConstraintConceptDofType.encastre())
    )

    # the "ada" logger does not propagate to the root logger where caplog listens
    ada_logger = logging.getLogger("ada")
    ada_logger.addHandler(caplog.handler)
    try:
        fem = p.to_fem_obj(0.1, GeomRepr.SHELL)
    finally:
        ada_logger.removeHandler(caplog.handler)

    assert [bc.name for bc in fem.bcs] == ["bm1_n1"]
    assert fem.bcs[0].dofs == [1, 2, 3]
    assert "overlaps beam end constraint(s) ['bm1_n1']" in caplog.text


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

    bcs = {bc.name: bc for bc in fem.bcs}
    xvec = to_global_vectors(bm, bm.xvec)
    p1, p2 = to_global_points(bm, [bm.n1.p, bm.n2.p])
    assert all(abs((n.p - p1) @ xvec) < 1e-4 for n in bcs["bm1_n1"].fem_set.members)
    assert all(abs((n.p - p2) @ xvec) < 1e-4 for n in bcs["bc2"].fem_set.members)
    if bm_repr != GeomRepr.LINE:
        assert len(bcs["bm1_n1"].fem_set.members) > 1
        assert len(bcs["bc2"].fem_set.members) > 1


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
