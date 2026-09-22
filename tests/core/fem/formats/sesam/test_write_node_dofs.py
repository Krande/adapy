"""Per-node NDOF/ODOF, and the records that have to agree with it.

ada wrote ``GNODE  nodex nodeno 6 123456`` for every node in every model. A solid
(continuum) element has no rotational stiffness, so a node touched only by solids then
has dofs 4-6 on the stiffness diagonal with nothing in them, and Sestra 10.16's reduction
aborts:

    REDUCTION MODULE - SUPERELEMENT TYPE 1
    Error in factorisation of Stiffness matrix.
    Matrix is not positive definite.
    The error may be related to External Node no:  278687
    Degree of freedom no:   4

The Input Interface File manual (printed 5-91) states the rule directly: NDOF and ODOF
"must be consistent with the type of node" -- solid type NDOF=3 / ODOF=123, shell type
NDOF=6 / ODOF=123456. (The same page is why a *shell* node stays 6-dof even though its
drilling rotation carries no stiffness either: shell elements insert a small term there.)

GNODE is not the only record with an NDOF field. BNBCD, BNMASS and BNLOAD each declare
one and then carry exactly that many values, so all four read the same
:class:`writer.NodeDofs`; a 3-dof dependent node's BNBCD is ``3`` + ``3 3 3``, not ``6`` +
``3 3 3 0 0 0``. Naming dof 4, 5 or 6 of a 3-dof node -- a boundary condition, a rotary
inertia, a moment, a BLDEP term -- raises instead of being written as a deck that
contradicts itself.

Note on authority: DNV's own GeniE writes every node 6/123456 in the T100.FEM available
here, but that model contains no solid elements at all, so it says nothing about this
case. The manual and the Sestra failure do.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.api.containers import Nodes
from ada.api.mesh.containers import to_array_backed
from ada.fem import FEM, Bc, Constraint, Elem, FemSet, Mass, Surface
from ada.fem.containers import FemElements
from ada.fem.formats.sesam.read import cards
from ada.fem.formats.sesam.write.write_bcs import (
    RETAINED,
    RETAINED_KEY,
    SUPERNODE_SET_NAME,
    bnbcd_str,
    retained_dofs_from_metadata,
)
from ada.fem.formats.sesam.write.write_constraints import bldep_records
from ada.fem.formats.sesam.write.write_loads import load_force
from ada.fem.formats.sesam.write.write_masses import mass_str
from ada.fem.formats.sesam.write.writer import (
    ALL_SIX_DOF,
    NodeDofs,
    node_dofs,
    nodes_str,
    odof_for,
)
from ada.fem.loads import Load
from ada.fem.shapes.definitions import LineShapes, MassTypes, ShellShapes, SolidShapes

# Both mesh substrates: the object Node/Elem containers, and the packed-array ones the
# converter actually uses (``Config().meshing_array_backed`` defaults on). They reach the
# answer by different routes -- a fancy-index write per block vs. a walk over elements --
# so every mesh-shaped case below is pinned on both.
PATHS = ["object", "array"]


def _finish(fem: FEM, path: str) -> FEM:
    if path == "array":
        to_array_backed(fem)
    return fem


def _hex_nodes(z0: float, z1: float, first_id: int) -> list[ada.Node]:
    """Eight nodes of a unit HEX8, bottom face then top face."""
    corners = [(0, 0), (1, 0), (1, 1), (0, 1)]
    return [
        ada.Node((x, y, z), first_id + i + 4 * layer)
        for layer, z in enumerate((z0, z1))
        for i, (x, y) in enumerate(corners)
    ]


def _solid_only_fem(path: str = "object") -> FEM:
    nodes = _hex_nodes(0.0, 1.0, 1)
    fem = FEM("solid", nodes=Nodes(nodes), elements=FemElements([Elem(1, nodes, SolidShapes.HEX8)]))
    return _finish(fem, path)


def _shell_only_fem(path: str = "object") -> FEM:
    nodes = [ada.Node((0, 0, 0), 1), ada.Node((1, 0, 0), 2), ada.Node((1, 1, 0), 3), ada.Node((0, 1, 0), 4)]
    fem = FEM("shell", nodes=Nodes(nodes), elements=FemElements([Elem(1, nodes, ShellShapes.QUAD)]))
    return _finish(fem, path)


def _beam_fem(path: str = "object") -> FEM:
    nodes = [ada.Node((0, 0, 0), 1), ada.Node((1, 0, 0), 2)]
    fem = FEM("beam", nodes=Nodes(nodes), elements=FemElements([Elem(1, nodes, LineShapes.LINE)]))
    return _finish(fem, path)


def _mixed_fem(path: str = "object") -> FEM:
    """A shell quad sitting on the top face of a HEX8: nodes 5-8 are shared, 1-4 are the
    solid's own bottom face."""
    solid = _hex_nodes(0.0, 1.0, 1)
    shell = solid[4:]
    fem = FEM(
        "mixed",
        nodes=Nodes(solid),
        elements=FemElements([Elem(1, solid, SolidShapes.HEX8), Elem(2, shell, ShellShapes.QUAD)]),
    )
    return _finish(fem, path)


def _solid_with_mass_fem(mass_value, mass_type, path: str = "object", node_id: int = 5) -> FEM:
    fem = _solid_only_fem("object")
    fs = fem.add_set(FemSet("ms", [fem.nodes.from_id(node_id)], FemSet.TYPES.NSET))
    fem.add_mass(Mass("m1", fs, mass_value, mass_type, parent=fem))
    return _finish(fem, path)


def _shell_to_solid_fem(swap_sides: bool = False) -> FEM:
    """A shell quad above a HEX8, the two not sharing nodes, coupled with SHELL2SOLID.

    ``swap_sides`` makes the *solid* face the master, which is the invalid case: the rigid
    link reads the master's rotations, and a solid node has none.
    """
    shell = [ada.Node((0, 0, 1), 1), ada.Node((1, 0, 1), 2), ada.Node((1, 1, 1), 3), ada.Node((0, 1, 1), 4)]
    solid = _hex_nodes(-2.0, -1.0, 11)
    fem = FEM(
        "s2s",
        nodes=Nodes(shell + solid),
        elements=FemElements([Elem(1, shell, ShellShapes.QUAD), Elem(2, solid, SolidShapes.HEX8)]),
    )
    edge = fem.add_set(FemSet("edge", [fem.nodes.from_id(i) for i in (1, 2)], "nset"))
    face = fem.add_set(FemSet("face", [fem.nodes.from_id(i) for i in (15, 16)], "nset"))
    if swap_sides:
        edge, face = face, edge
    m = Surface("m_surf", Surface.TYPES.NODE, edge, parent=fem)
    s = Surface("s_surf", Surface.TYPES.NODE, face, parent=fem)
    fem.add_constraint(Constraint("s2s", Constraint.TYPES.SHELL2SOLID, m, s, parent=fem))
    return fem


def _gnode(text: str) -> dict[int, tuple[int, int]]:
    """{node id: (NDOF, ODOF)} out of a GNODE block."""
    out = {}
    for m in cards.GNODE.to_ff_re().finditer(text):
        d = m.groupdict()
        out[int(float(d["nodeno"]))] = (int(float(d["ndof"])), int(float(d["odof"])))
    return out


def _bnbcd(text: str) -> dict[int, tuple[int, list[int]]]:
    """{node id: (NDOF, the codes actually written)} out of a BNBCD block."""
    out = {}
    for m in cards.re_bnbcd.finditer(text):
        d = m.groupdict()
        out[int(float(d["nodeno"]))] = (int(float(d["ndof"])), [int(float(x)) for x in d["content"].split()])
    return out


def _bnmass(text: str) -> dict[int, tuple[int, list[float]]]:
    out = {}
    for m in cards.re_bnmass.finditer(text):
        d = m.groupdict()
        out[int(float(d["nodeno"]))] = (int(float(d["ndof"])), [float(x) for x in d["content"].split()])
    return out


# ── the mesh rule ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", PATHS)
def test_solid_only_nodes_are_3_dof(path):
    """The defect. Every node of a solid-only mesh is 3/123, where the writer used to say
    6/123456 and hand Sestra eight zero-stiffness rotations."""
    dofs = node_dofs(_solid_only_fem(path))
    assert [dofs.ndof(nid) for nid in range(1, 9)] == [3] * 8


@pytest.mark.parametrize("path", PATHS)
def test_shell_only_nodes_are_6_dof(path):
    dofs = node_dofs(_shell_only_fem(path))
    assert [dofs.ndof(nid) for nid in range(1, 5)] == [6] * 4


@pytest.mark.parametrize("path", PATHS)
def test_beam_nodes_are_6_dof(path):
    dofs = node_dofs(_beam_fem(path))
    assert [dofs.ndof(nid) for nid in (1, 2)] == [6, 6]


@pytest.mark.parametrize("path", PATHS)
def test_mixed_mesh_is_6_dof_only_where_the_shell_reaches(path):
    """Rotations exist exactly where something that has them is attached: the four shared
    nodes, not the solid interior."""
    dofs = node_dofs(_mixed_fem(path))
    assert {nid: dofs.ndof(nid) for nid in range(1, 9)} == {1: 3, 2: 3, 3: 3, 4: 3, 5: 6, 6: 6, 7: 6, 8: 6}


@pytest.mark.parametrize("path", PATHS)
def test_rotary_inertia_promotes_an_otherwise_solid_only_node(path):
    """A rotary inertia is written into BNMASS dofs 4-6, so the node it sits on genuinely
    has them -- even with nothing but solids around it."""
    fem = _solid_with_mass_fem([1.0, 2.0, 3.0], MassTypes.ROTARYI, path)
    dofs = node_dofs(fem)
    assert dofs.ndof(5) == 6
    assert [dofs.ndof(nid) for nid in (1, 2, 3, 4, 6, 7, 8)] == [3] * 7


@pytest.mark.parametrize("path", PATHS)
def test_translational_mass_does_not_promote_a_solid_only_node(path):
    """The converse: a plain point mass only loads dofs 1-3, so the node stays 3-dof. Had
    this promoted, every mass in a solid model would have re-created the defect locally."""
    fem = _solid_with_mass_fem(12.0, MassTypes.MASS, path)
    assert [node_dofs(fem).ndof(nid) for nid in range(1, 9)] == [3] * 8


@pytest.mark.parametrize("path", PATHS)
def test_unattached_node_stays_6_dof(path):
    """A node no element touches is not "a solid node" -- it is an unused node, and 6 is
    what the writer has always said about it. (It has no stiffness either way.)"""
    nodes = _hex_nodes(0.0, 1.0, 1) + [ada.Node((9, 9, 9), 99)]
    fem = FEM("lonely", nodes=Nodes(nodes), elements=FemElements([Elem(1, nodes[:8], SolidShapes.HEX8)]))
    dofs = node_dofs(_finish(fem, path))
    assert dofs.ndof(99) == 6 and dofs.ndof(1) == 3


# ── GNODE ────────────────────────────────────────────────────────────────────────


def test_gnode_pairs_ndof_with_the_right_odof():
    """Manual printed 5-91: solid type 3/123, shell type 6/123456. ODOF is the dof list,
    so it cannot be left at 123456 on a 3-dof node."""
    assert _gnode(nodes_str(_solid_only_fem())) == {nid: (3, 123) for nid in range(1, 9)}
    assert _gnode(nodes_str(_shell_only_fem())) == {nid: (6, 123456) for nid in range(1, 5)}


def test_gnode_of_a_mixed_mesh():
    written = _gnode(nodes_str(_mixed_fem()))
    assert {nid: written[nid] for nid in (1, 4)} == {1: (3, 123), 4: (3, 123)}
    assert {nid: written[nid] for nid in (5, 8)} == {5: (6, 123456), 8: (6, 123456)}


def test_nodes_str_still_streams_and_still_rejects_duplicate_ids():
    """The two behaviours ``nodes_gen`` had before per-node NDOF: one record per node, in
    id order, and a duplicate id is an error rather than a silently doubled node."""
    fem = _solid_only_fem()
    ids = [int(float(ln.split()[1])) for ln in nodes_str(fem).splitlines() if ln.startswith("GNODE")]
    assert ids == sorted(ids) == list(range(1, 9))

    dupe = FEM("dupe", nodes=Nodes([ada.Node((0, 0, 0), 1), ada.Node((1, 0, 0), 1)]))
    with pytest.raises(Exception, match="Doubly defined node id"):
        nodes_str(dupe)


def test_odof_for_rejects_anything_but_3_or_6():
    assert (odof_for(3), odof_for(6)) == (123, 123456)
    with pytest.raises(ValueError, match="either 3 or 6 dofs"):
        odof_for(4)


# ── BNBCD ────────────────────────────────────────────────────────────────────────


def test_bnbcd_code_count_follows_ndof():
    """A 3-dof BLDEP dependent emits ``3`` and three codes. Padding it to six would
    declare rotations GNODE says the node does not have."""
    fem = _shell_to_solid_fem()
    dofs = node_dofs(fem)
    written = _bnbcd(bnbcd_str([fem], bldep_records(fem), None, dofs))

    for nid in (15, 16):  # the solid face: dependent on the shell edge
        assert written[nid] == (3, [3, 3, 3]), "three codes, not 3 3 3 0 0 0"
    for nid in (1, 2):  # the shell edge: masters, explicitly free
        assert written[nid] == (6, [0] * 6)
    for ndof, codes in written.values():
        assert len(codes) == ndof


def test_bnbcd_defaults_to_six_dofs_when_handed_no_node_dofs():
    """The signature stayed backwards compatible: no ``ndofs`` means what this function
    meant before -- every node 6-dof. ``to_fem`` always passes the real thing."""
    fem = _shell_to_solid_fem()
    written = _bnbcd(bnbcd_str([fem], bldep_records(fem)))
    assert written[15] == (6, [3, 3, 3, 0, 0, 0])
    assert bnbcd_str([fem], bldep_records(fem), None, ALL_SIX_DOF) == bnbcd_str([fem], bldep_records(fem))


def test_bc_on_a_rotational_dof_of_a_solid_node_raises():
    fem = _solid_only_fem()
    fem.add_bc(Bc("clamp", fem.add_set(FemSet("c", [fem.nodes.from_id(3)], "nset")), [1, 2, 3, 4, 5, 6]))
    with pytest.raises(ValueError, match=r'boundary condition "clamp" names dof 4 of node 3, which has 3 dofs'):
        bnbcd_str([fem], (), None, node_dofs(fem))


def test_bc_on_translations_of_a_solid_node_is_fine():
    fem = _solid_only_fem()
    fem.add_bc(Bc("clamp", fem.add_set(FemSet("c", [fem.nodes.from_id(3)], "nset")), [1, 2, 3]))
    assert _bnbcd(bnbcd_str([fem], (), None, node_dofs(fem))) == {3: (3, [1, 1, 1])}


def test_retained_dof_above_a_nodes_ndof_raises():
    """A supernode dof has to exist before it can be part of the assembly interface."""
    fem = _solid_only_fem()
    fem.add_set(FemSet("Super", [fem.nodes.from_id(2)], "nset"))
    retained = retained_dofs_from_metadata([fem], {RETAINED_KEY: {"Super": [1, 2, 3, 4, 5, 6]}})
    with pytest.raises(ValueError, match=r"retained \(supernode\) set names dof 4 of node 2"):
        bnbcd_str([fem], (), retained, node_dofs(fem))


def test_bldep_master_dof_above_the_masters_ndof_raises():
    """A rigid link reads the master's rotations. Make the solid face the master and those
    rotations do not exist -- which is a modelling error, not something to truncate."""
    fem = _shell_to_solid_fem(swap_sides=True)
    records = bldep_records(fem)
    assert any(dof > 3 for r in records for dof in r.master_dofs), "the fixture must exercise a master rotation"
    with pytest.raises(ValueError, match=r"BLDEP master node 15 .* names dof 4 of node 15, which has 3 dofs"):
        bnbcd_str([fem], records, None, node_dofs(fem))


def test_bldep_dependent_dof_above_its_ndof_raises():
    """The dependent side of the same rule, driven straight off a record so it does not
    depend on what ``LinDep`` happens to produce."""
    from ada.fem.formats.sesam.write.write_constraints import BldepRecord

    fem = _shell_to_solid_fem()
    record = BldepRecord(slave=15, master=1, terms=((5, 5, 1.0),))
    with pytest.raises(ValueError, match=r"BLDEP dependent node 15 .* names dof 5 of node 15, which has 3 dofs"):
        bnbcd_str([fem], [record], None, node_dofs(fem))


# ── BNMASS ───────────────────────────────────────────────────────────────────────


def test_bnmass_on_a_3_dof_node_carries_three_components():
    fem = _solid_with_mass_fem(12.0, MassTypes.MASS)
    text = mass_str(fem, node_dofs(fem))
    assert _bnmass(text) == {5: (3, [12.0, 12.0, 12.0])}
    # and the reader gets it back, so the writer is not producing a deck ada can't read
    assert int(float(next(cards.re_bnmass.finditer(text)).groupdict()["ndof"])) == 3


def test_bnmass_on_a_6_dof_node_is_unchanged():
    fem = _solid_with_mass_fem([1.0, 2.0, 3.0], MassTypes.ROTARYI)
    assert _bnmass(mass_str(fem, node_dofs(fem))) == {5: (6, [0.0, 0.0, 0.0, 1.0, 2.0, 3.0])}


def test_rotary_inertia_written_onto_a_3_dof_node_raises():
    """The guard, driven with a NodeDofs that disagrees with the mass. ``node_dofs`` would
    never say this -- a rotary inertia promotes its node -- but the check has to be at the
    record, because that is where a truncated BNMASS would silently drop the inertia."""
    fem = _solid_with_mass_fem([1.0, 2.0, 3.0], MassTypes.ROTARYI)
    lying = NodeDofs(np.array([5], dtype=np.int64), np.array([False]))
    with pytest.raises(ValueError, match=r"rotary inertia on dof\(s\) \[4, 5, 6\] of node 5"):
        mass_str(fem, lying)


# ── BNLOAD ───────────────────────────────────────────────────────────────────────


def _force_load(fem: FEM, node_id: int, forces: list[float]) -> Load:
    """A point load whose ``forces`` are exactly the six values given (magnitude 1)."""
    fs = fem.add_set(FemSet("ls", [fem.nodes.from_id(node_id)], "nset"))
    return Load("L", Load.TYPES.FORCE, 1.0, fem_set=fs, dof=list(forces))


def test_bnload_on_a_3_dof_node_carries_three_components():
    fem = _solid_only_fem()
    dofs = node_dofs(fem)
    text = load_force(_force_load(fem, 2, [10.0, 20.0, 30.0, 0.0, 0.0, 0.0]), 1, dofs)
    rows = [ln.split() for ln in text.splitlines()]
    assert int(float(rows[1][1])) == 3, "NDOF field"
    assert [float(x) for x in rows[1][2:]] + [float(x) for x in rows[2]] == [10.0, 20.0, 30.0]


def test_moment_load_on_a_3_dof_node_raises():
    fem = _solid_only_fem()
    with pytest.raises(ValueError, match=r"moment on dof\(s\) \[6\] of node 2, which has 3 dofs"):
        load_force(_force_load(fem, 2, [0.0, 0.0, 0.0, 0.0, 0.0, 7.0]), 1, node_dofs(fem))


def test_moment_load_on_a_6_dof_node_is_written_in_full():
    fem = _shell_only_fem()
    text = load_force(_force_load(fem, 2, [0.0, 0.0, 1.0, 0.0, 0.0, 7.0]), 1, node_dofs(fem))
    rows = [ln.split() for ln in text.splitlines()]
    assert int(float(rows[1][1])) == 6
    assert [float(x) for x in rows[1][2:]] + [float(x) for x in rows[2]] == [0.0, 0.0, 1.0, 0.0, 0.0, 7.0]


# ── NodeDofs itself ──────────────────────────────────────────────────────────────


def test_node_dofs_lookup_is_order_independent_and_vectorised():
    """``ndof_many`` is what the 689k-node GNODE block uses; it must agree with the
    per-node binary search, and both must cope with unsorted input ids."""
    dofs = NodeDofs(np.array([7, 3, 11], dtype=np.int64), np.array([True, False, True]))
    assert [dofs.ndof(nid) for nid in (3, 7, 11)] == [3, 6, 6]
    assert list(dofs.ndof_many([11, 3, 7, 3])) == [6, 3, 6, 3]
    # A node this object never saw is reported as 6 -- the historical answer, and the only
    # safe one for e.g. a load held on the assembly FEM naming a part node.
    assert dofs.ndof(999) == 6 and list(dofs.ndof_many([999, 3])) == [6, 3]
    assert len(ALL_SIX_DOF) == 0 and ALL_SIX_DOF.ndof(1) == 6


# ── end to end ───────────────────────────────────────────────────────────────────


def test_solid_box_export_writes_3_dof_everywhere(tmp_path):
    """The whole writer, on a real meshed solid: every GNODE is 3/123, and every record
    that repeats an NDOF agrees with it."""
    box = ada.PrimBox("box", (0, 0, 0), (1, 1, 1))
    fem = box.to_fem_obj(0.5, "solid", use_hex=True)
    (ada.Assembly("a") / (ada.Part("p", fem=fem) / box)).to_fem(
        "m", fem_format="sesam", scratch_dir=tmp_path, overwrite=True
    )
    text = (tmp_path / "m" / "mT1.FEM").read_text()

    written = _gnode(text)
    assert written and set(written.values()) == {(3, 123)}
    for nid, (ndof, codes) in _bnbcd(text).items():
        assert (ndof, len(codes)) == (written[nid][0], written[nid][0])


def test_shell_plate_export_is_unchanged_at_6_dof(tmp_path):
    """The regression guard on the other side: a shell model's GNODE block must look
    exactly as it always has."""
    pl = ada.Plate("pl", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01)
    fem = pl.to_fem_obj(0.5, "shell")
    (ada.Assembly("a") / (ada.Part("p", fem=fem) / pl)).to_fem(
        "m", fem_format="sesam", scratch_dir=tmp_path, overwrite=True
    )
    written = _gnode((tmp_path / "m" / "mT1.FEM").read_text())
    assert written and set(written.values()) == {(6, 123456)}


def test_supernode_convention_on_a_solid_only_node_retains_only_its_three_dofs(tmp_path):
    """The convention and the per-node DOF count have to agree with each other.

    A solid-only node has three DOFs. "Retain all six" is the writer's own inference, not
    something the caller asked for, so on such a node it has to mean 1-3 — otherwise the
    convention hands out DOFs 4-6 and ``bnbcd_str`` rejects the writer's own output with
    "the retained set names dof 4 of node N, which has 3 dofs". Reachable from any model
    whose superelement interface sits on solid nodes.
    """
    box = ada.PrimBox("box", (0, 0, 0), (1, 1, 1))
    fem = box.to_fem_obj(0.5, "solid", use_hex=True)
    interface = [n for n in fem.nodes][:4]
    fem.add_set(FemSet(SUPERNODE_SET_NAME, interface, "nset"))

    (ada.Assembly("a") / (ada.Part("p", fem=fem) / box)).to_fem(
        "m", fem_format="sesam", scratch_dir=tmp_path, overwrite=True
    )
    text = (tmp_path / "m" / "mT1.FEM").read_text()

    gnode, bnbcd = _gnode(text), _bnbcd(text)
    retained = {nid: codes for nid, (_, codes) in bnbcd.items() if RETAINED in codes}
    assert set(retained) == {n.id for n in interface}
    for nid, codes in retained.items():
        assert gnode[nid] == (3, 123)
        assert codes == [RETAINED] * 3, "three dofs, all retained -- not six"


def test_explicit_metadata_naming_a_missing_dof_still_raises(tmp_path):
    """The clamp applies to the convention only. Asking outright for a rotation on a node
    that has none is an impossible request, and stays an error."""
    box = ada.PrimBox("box", (0, 0, 0), (1, 1, 1))
    fem = box.to_fem_obj(0.5, "solid", use_hex=True)
    interface = [n for n in fem.nodes][:4]
    fem.add_set(FemSet("IFACE", interface, "nset"))

    a = ada.Assembly("a") / (ada.Part("p", fem=fem) / box)
    with pytest.raises(ValueError, match="dof 4"):
        a.to_fem(
            "m",
            fem_format="sesam",
            scratch_dir=tmp_path,
            overwrite=True,
            metadata={RETAINED_KEY: {"IFACE": [1, 2, 3, 4, 5, 6]}},
        )
