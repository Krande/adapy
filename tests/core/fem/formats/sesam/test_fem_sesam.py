import pytest

import ada
from ada.fem.containers import FemElements
from ada.fem.formats.sesam.read.read_sets import SetReader
from ada.fem.formats.sesam.write.write_sets import sets_str
from ada.fem.formats.sesam.write.writer import nodes_str, write_ff
from ada.fem.shapes.definitions import LineShapes


def test_write_ff():
    flag = "TDMATER"
    data = [
        (1, 1, 0, 0),
        (83025, 4, 0, 3),
        (0.4870624787676558, 0.4870624787676558, 0.4870624787676558, 0.4870624787676558),
    ]
    test_str = write_ff(flag, data)
    fflag = "BEUSLO"
    ddata = [
        (1, 1, 0, 0),
        (83025, 4, 0, 3),
        (0.4870624787676558, 0.4870624787676558, 0.4870624787676558, 0.4870624787676558),
    ]
    test_str += write_ff(fflag, ddata)
    # print(test_str)


def test_write_sets():

    elements = [
        ada.fem.Elem(el_id, [ada.Node((el_id, 0, 0), el_id), ada.Node((el_id + 1, 0, 0), el_id + 1)], LineShapes.LINE)
        for el_id in range(1, 2000)
    ]
    fem = ada.FEM("MyFem", elements=FemElements(elements))
    original_set = fem.add_set(ada.fem.FemSet("MySet", elements))
    result_str = sets_str(fem)

    return_fem = ada.FEM("MyFem", elements=fem.elements)
    sr = SetReader(result_str, return_fem)
    roundtripped_sets = sr.run()
    assert len(roundtripped_sets) == 1

    roundtripped_set = roundtripped_sets[0]
    assert len(original_set.members) == len(roundtripped_set.members)


def test_write_ff_keeps_repeated_rows():
    """A record whose intermediate row equals its last row must still be continued."""
    rows = [(1, 2, 3, 4), (0, 0, 0, 0), (0, 0, 0, 0)]
    out = write_ff("GELREF1", rows)
    lines = out.splitlines()
    assert len(lines) == len(rows)
    assert out.endswith("\n") and out.count("\n") == len(rows)
    # every row but the last is continued, i.e. followed by the 8-space continuation
    assert lines[0].startswith("GELREF1")
    for line in lines[1:]:
        assert line.startswith(" " * 8)


def test_nodes_str_rejects_duplicate_ids():
    nodes = [ada.Node((0, 0, 0), 1), ada.Node((1, 0, 0), 2)]
    fem = ada.FEM("MyFem", nodes=ada.api.containers.Nodes(nodes + [ada.Node((2, 0, 0), 1)]))
    with pytest.raises(Exception, match="Doubly defined node id"):
        nodes_str(fem)


def test_nodes_str_emits_every_node_sorted_by_id():
    ids = [7, 3, 11, 1]
    fem = ada.FEM("MyFem", nodes=ada.api.containers.Nodes([ada.Node((i, 0, 0), i) for i in ids]))
    gnodes = [ln for ln in nodes_str(fem).splitlines() if ln.startswith("GNODE")]
    assert len(gnodes) == len(ids)
    emitted = [int(float(ln.split()[1])) for ln in gnodes]
    assert emitted == sorted(ids)


def _shell2solid_fem():
    """A shell edge (2 nodes) meeting a solid face (4 nodes, offset in z)."""
    from ada.fem import Constraint, Surface

    shell_nodes = [ada.Node((0, 0, 0), 1), ada.Node((1, 0, 0), 2)]
    solid_nodes = [
        ada.Node((0, 0, -0.5), 11),
        ada.Node((0, 0, 0.5), 12),
        ada.Node((1, 0, -0.5), 13),
        ada.Node((1, 0, 0.5), 14),
    ]
    fem = ada.FEM("MyFem", nodes=ada.api.containers.Nodes(shell_nodes + solid_nodes))
    edge = fem.add_set(ada.fem.FemSet("edge", shell_nodes, "nset"))
    face = fem.add_set(ada.fem.FemSet("face", solid_nodes, "nset"))
    m = Surface("edge_surf", Surface.TYPES.NODE, edge, parent=fem)
    s = Surface("face_surf", Surface.TYPES.NODE, face, parent=fem)
    constraint = Constraint("s2s", Constraint.TYPES.SHELL2SOLID, m, s, parent=fem)
    fem.add_constraint(constraint)
    return fem, constraint


def test_shell2solid_writes_one_bldep_per_solid_node():
    from ada.fem.formats.sesam.write.write_constraints import write_shell2solid

    _, constraint = _shell2solid_fem()
    out = write_shell2solid(constraint)

    records = [ln for ln in out.splitlines() if ln.startswith("BLDEP")]
    assert len(records) == 4  # one per solid-face node
    # SLAVE MASTER NDDOF NDEP -- the solid node depends on the shell node, not the reverse
    slaves, masters = set(), set()
    for rec in records:
        slave, master, nddof, ndep = (int(float(x)) for x in rec.split()[1:5])
        assert (nddof, ndep) == (3, 9)
        slaves.add(slave)
        masters.add(master)
    assert slaves == {11, 12, 13, 14}
    assert masters == {1, 2}  # each paired with the nearest shell-edge node


def test_shell2solid_lever_arm_coefficients():
    """Slave dof 1 (x) picks up master dof 5 (Ry) with beta = dz."""
    from ada.fem.formats.sesam.write.write_constraints import write_shell2solid

    _, constraint = _shell2solid_fem()
    lines = write_shell2solid(constraint).splitlines()
    # first record: slave 11 at z=-0.5 under master 1 at z=0 -> dz = -0.5
    assert int(float(lines[0].split()[1])) == 11
    dof_s, dof_m, beta = (float(x) for x in lines[2].split()[:3])
    assert (int(dof_s), int(dof_m)) == (1, 5)
    assert beta == -0.5


def test_surface_nodes_resolves_named_sets_via_id_refs():
    """A surface listing several sets by name resolves through the parent FEM."""
    from ada.fem import Surface
    from ada.fem.surfaces import surface_nodes

    nodes = [ada.Node((i, 0, 0), i + 1) for i in range(4)]
    fem = ada.FEM("MyFem", nodes=ada.api.containers.Nodes(nodes))
    fem.add_set(ada.fem.FemSet("a", nodes[:2], "nset"))
    fem.add_set(ada.fem.FemSet("b", nodes[2:], "nset"))
    surf = Surface("s", Surface.TYPES.NODE, None, id_refs=[("a", ""), ("b", "")], parent=fem)
    assert sorted(n.id for n in surface_nodes(surf)) == [1, 2, 3, 4]


def test_shell2solid_bldep_is_readable_by_the_sesam_reader():
    from ada.fem.formats.sesam.read import cards
    from ada.fem.formats.sesam.write.write_constraints import write_shell2solid
    from ada.fem.formats.utils import str_to_int

    _, constraint = _shell2solid_fem()
    parsed = [m.groupdict() for m in cards.re_bldep.finditer(write_shell2solid(constraint))]

    assert len(parsed) == 4
    for d in parsed:
        assert str_to_int(d["nddof"]) == 3
        assert str_to_int(d["ndep"]) == 9
        assert len(d["bulk"].split()) // 4 == 9
    assert {str_to_int(d["slave"]): str_to_int(d["master"]) for d in parsed} == {11: 1, 12: 1, 13: 2, 14: 2}


def test_surface_based_coupling_writes_bldep():
    """Abaqus writes *Coupling with surface=, so a coupling's sides arrive as Surface
    rather than FemSet. Reading .members straight off one raised AttributeError."""
    from ada.fem import Constraint, Surface
    from ada.fem.formats.sesam.write.write_constraints import constraint_str

    ref = [ada.Node((0, 0, 1), 1)]
    region = [ada.Node((1, 0, 0), 2), ada.Node((0, 1, 0), 3)]
    fem = ada.FEM("MyFem", nodes=ada.api.containers.Nodes(ref + region))
    m = Surface("ref_surf", Surface.TYPES.NODE, fem.add_set(ada.fem.FemSet("m", ref, "nset")), parent=fem)
    s = Surface("reg_surf", Surface.TYPES.NODE, fem.add_set(ada.fem.FemSet("s", region, "nset")), parent=fem)
    fem.add_constraint(Constraint("cpl", Constraint.TYPES.COUPLING, m, s, parent=fem))

    records = [ln for ln in constraint_str(fem).splitlines() if ln.startswith("BLDEP")]
    assert len(records) == 2, "one BLDEP per slave node"
    assert {int(float(r.split()[1])) for r in records} == {2, 3}
    assert {int(float(r.split()[2])) for r in records} == {1}, "all slaves hang off the reference node"


def test_coupling_still_accepts_plain_femsets():
    from ada.fem import Constraint
    from ada.fem.formats.sesam.write.write_constraints import constraint_str

    ref = [ada.Node((0, 0, 1), 1)]
    region = [ada.Node((1, 0, 0), 2)]
    fem = ada.FEM("MyFem", nodes=ada.api.containers.Nodes(ref + region))
    m = fem.add_set(ada.fem.FemSet("m", ref, "nset"))
    s = fem.add_set(ada.fem.FemSet("s", region, "nset"))
    fem.add_constraint(Constraint("cpl", Constraint.TYPES.COUPLING, m, s, parent=fem))

    assert len([ln for ln in constraint_str(fem).splitlines() if ln.startswith("BLDEP")]) == 1


def test_rigid_body_over_an_element_region_still_flattens_to_nodes():
    """A rigid body whose region is an element set must resolve to that set's nodes."""
    from ada.fem import Constraint
    from ada.fem.formats.sesam.write.write_constraints import constraint_str
    from ada.fem.shapes.definitions import LineShapes

    nodes = [ada.Node((0, 0, 1), 1), ada.Node((1, 0, 0), 2), ada.Node((2, 0, 0), 3)]
    fem = ada.FEM("MyFem", nodes=ada.api.containers.Nodes(nodes))
    el = ada.fem.Elem(1, [nodes[1], nodes[2]], LineShapes.LINE, parent=fem)
    fem.elements = ada.fem.containers.FemElements([el], fem_obj=fem)
    m = fem.add_set(ada.fem.FemSet("m", [nodes[0]], "nset"))
    s = fem.add_set(ada.fem.FemSet("s", [el], "elset"))
    fem.add_constraint(Constraint("rb", Constraint.TYPES.RIGID_BODY, m, s, parent=fem))

    records = [ln for ln in constraint_str(fem).splitlines() if ln.startswith("BLDEP")]
    assert {int(float(r.split()[1])) for r in records} == {2, 3}
