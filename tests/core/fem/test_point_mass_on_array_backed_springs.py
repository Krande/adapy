"""A point mass on a node a spring or a connector alone holds is held on the array-backed mesh too.

The Sesam and Abaqus readers (and ``to_array_backed``) give an array-backed mesh, where ``Node.refs`` is a view over
the store: the elements come from the block connectivity, everything else from a side-table. A spring or a connector
has no block row there: the readers and ``FEM.add_spring`` put it in ``ArrayElements._overflow``, and
``RefsView.append`` skipped every ``Elem``, so it was in neither. ``node_is_held`` then found a mass on a grounded
spring free: the Code_Aster writer left the mass out (and the rotary inertia, as ``_has_rotations`` read the refs the
same way), and Sesam and CalculiX reported it suspect, although the model is sound. A spring ``to_array_backed`` packs
into a block is in the connectivity, as an ``ElemProxy`` with no stiffness; ``_has_rotations`` finds its ``Spring``.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.api.mesh.containers import to_array_backed
from ada.api.mesh.proxies import NodeProxy
from ada.config import Config
from ada.fem import Bc, Connector, ConnectorSection, FemSet, Mass, Spring, StepEigen
from ada.fem.formats import conversion_report
from ada.fem.formats.code_aster.write.write_masses import _has_rotations
from ada.fem.utils import free_point_masses, node_is_held

MASS = 250.0
PATHS = ["object", "to_array_backed", "added_after", "sesam_deck", "abaqus_deck"]


def _spring_assembly(path: str, tmp_path, monkeypatch) -> ada.Assembly:
    """One node on a grounded 6 x 6 spring (SPRING1), with a point mass and a rotary inertia on it."""
    p = ada.Part("P")
    fem = p.fem
    node = fem.nodes.add(ada.Node((0.0, 0.0, 0.0), 1))
    if path == "added_after":  # the reader's order: the mesh is array-backed before the spring and masses arrive
        to_array_backed(fem)
        node = fem.nodes.from_id(1)
    stiff = np.diag([1.0e6, 3.0e6, 9.0e6, 1.0e9, 1.0e9, 1.0e9])
    fem.add_spring(Spring("spr", 1, "SPRING1", stiff, fem.add_set(FemSet("spr_n", [node], "nset"))))
    fem.add_mass(Mass("m", [node], MASS, mass_id=2))
    fem.add_mass(Mass("rot", [node], [1.0, 1.0, 1.0, 0.0, 0.0, 0.0], mass_type="ROTARYI", mass_id=3))
    a = ada.Assembly("A") / p
    if path == "to_array_backed":
        to_array_backed(fem)
    elif path.endswith("_deck"):
        fmt = path.removesuffix("_deck")
        a.to_fem("deck", fmt, scratch_dir=tmp_path, overwrite=True)
        deck = next(f for f in (tmp_path / "deck").iterdir() if f.suffix.lower() in (".fem", ".inp"))
        monkeypatch.setattr(Config(), "meshing_array_backed", True)
        a = ada.from_fem(deck, fmt)
    a.fem.add_step(StepEigen("eig", num_eigen_modes=3))
    return a


def _the_fem(a: ada.Assembly):
    (fem,) = [p.fem for p in a.get_all_parts_in_assembly() if len(p.fem.elements.masses)]
    return fem


def _point_mass(fem) -> Mass:
    (mass,) = [m for m in fem.elements.masses if m.type == Mass.TYPES.MASS]
    return mass


def _mass_findings(rep, kind: str) -> list:
    return [f for f in rep.findings if f.keyword == "Mass" and f.kind == kind]


@pytest.mark.parametrize("path", PATHS)
def test_a_node_a_spring_alone_holds_is_held(tmp_path, monkeypatch, path):
    fem = _the_fem(_spring_assembly(path, tmp_path, monkeypatch))
    (node,) = _point_mass(fem).members
    assert isinstance(node, NodeProxy) == (path != "object")
    assert node_is_held(node)
    assert _has_rotations(node)  # the spring is 6 x 6
    assert free_point_masses(fem) == []


@pytest.mark.parametrize("path", PATHS)
def test_code_aster_writes_the_mass_and_inertia_on_a_spring(tmp_path, monkeypatch, path):
    a = _spring_assembly(path, tmp_path, monkeypatch)
    with conversion_report.collect() as rep:
        a.to_fem("ca", "code_aster", scratch_dir=tmp_path, overwrite=True)

    comm = (tmp_path / "ca" / "ca.comm").read_text()
    assert f"CARA='M_T_D_N', VALE={MASS!r}" in comm
    assert "CARA='M_TR_N'" in comm
    assert _mass_findings(rep, "omitted") == []


@pytest.mark.parametrize("fem_format", ["sesam", "calculix"])
@pytest.mark.parametrize("path", PATHS)
def test_sesam_and_calculix_do_not_report_a_mass_on_a_spring(tmp_path, monkeypatch, path, fem_format):
    a = _spring_assembly(path, tmp_path, monkeypatch)
    with conversion_report.collect() as rep:
        a.to_fem("out", fem_format, scratch_dir=tmp_path, overwrite=True)

    # (CalculiX leaves the rotary inertia out, as it has no form for one: omitted, not suspect)
    assert _mass_findings(rep, "suspect") == []


@pytest.mark.parametrize("path", ["object", "to_array_backed", "added_after"])
def test_a_node_a_connector_alone_holds_is_held(path):
    """A connector from a fixed node to the mass node: no other element on it."""
    p = ada.Part("P")
    fem = p.fem
    fixed = fem.nodes.add(ada.Node((0.0, 0.0, 0.0), 1))
    free = fem.nodes.add(ada.Node((1.0, 0.0, 0.0), 2))
    if path == "added_after":
        to_array_backed(fem)
        fixed, free = fem.nodes.from_id(1), fem.nodes.from_id(2)
    fem.add_bc(Bc("fix", fem.add_set(FemSet("fixed", [fixed], "nset")), [1, 2, 3, 4, 5, 6]))
    con_sec = ConnectorSection("con_sec", elastic_comp=1.0e6)
    fem.add_connector(Connector("con", 10, fixed, free, "bushing", con_sec))
    fem.add_mass(Mass("m", [free], MASS, mass_id=11))
    if path == "to_array_backed":
        to_array_backed(fem)
    node = _point_mass(fem).members[0]
    assert node_is_held(node)
    assert free_point_masses(fem) == []


def test_a_spring_stays_on_its_node_when_other_nodes_are_removed():
    """``MeshArrays.remove_nodes`` cleared the whole side-table: every set, spring and mass left ``Node.refs`` of the
    nodes that stayed (``merge_coincident`` removes nodes this way), so the node a spring alone holds looked free."""
    p = ada.Part("P")
    fem = p.fem
    fem.nodes.add(ada.Node((0.0, 0.0, 0.0), 1))
    fem.nodes.add(ada.Node((5.0, 0.0, 0.0), 2))
    to_array_backed(fem)
    keep = fem.nodes.from_id(2)
    fem.add_spring(Spring("spr", 1, "SPRING1", np.diag([1.0e6] * 6), fem.add_set(FemSet("spr_n", [keep], "nset"))))
    fem.add_mass(Mass("m", [keep], MASS, mass_id=2))
    fem.nodes.remove([fem.nodes.from_id(1)])  # shifts the remaining node's row

    (node,) = list(fem.nodes)
    assert np.allclose(node.p, (5.0, 0.0, 0.0))
    assert any(isinstance(r, Spring) for r in node.refs)
    assert node_is_held(node)


def test_remove_standalones_keeps_a_node_a_mass_alone_is_on():
    """The mass was in no ``Node.refs`` on the array-backed mesh, so its node looked unused and was removed, leaving
    the mass on a node the mesh no longer has."""
    bm = ada.Beam("bm", (0, 0, 0), (2, 0, 0), "IPE300")
    p = ada.Part("P") / bm
    p.fem = fem = bm.to_fem_obj(1.0, "line")
    to_array_backed(fem)
    fem.add_mass(Mass("m", [fem.nodes.add(ada.Node((2.0, 0.0, 1.0)))], MASS))

    fem.nodes.remove_standalones()

    assert [tuple(n.p) for n in fem.nodes if n.z == 1.0] == [(2.0, 0.0, 1.0)]
    assert any(isinstance(r, Mass) for r in _point_mass(fem).members[0].refs)
