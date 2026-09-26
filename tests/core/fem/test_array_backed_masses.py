"""A point mass has to survive ``to_array_backed``.

``ArrayElements.masses`` read only ``self._overflow``, the list that holds the special
elements a caller hands to ``add()``. But ``to_array_backed`` does not go through
``add()``: it builds the store with ``MeshArrays.from_fem``, which groups elements by
``Elem.type`` -- and a ``Mass``'s type is a ``MassTypes`` member like any other shape, so
the mass got packed into a block of its own. A block holds connectivity and element ids
and nothing else, so the packing was *lossy*, not merely invisible: ``mass._mass``, the
name, the point-mass type and the ``members`` list had nowhere to go and were dropped
with the object.

Nothing complained. ``fem.elements.masses`` simply went empty, and every writer that
iterates it -- Sesam ``MGMASS``, Abaqus ``*Mass``, Usfos ``NODEMASS`` -- wrote no record
at all. The mass was gone from the exported deck.

The fix keeps the objects: the packed rows stay in their blocks (so element iteration,
``len()``, ``group_by_type`` and the per-node NDOF derivation are untouched) and the
``Mass``/``Spring``/``Connector`` objects are retained beside them, with their node
references re-pointed at the substrate. Every case below is therefore asserted to be
*identical* on the two paths, not merely non-empty on the array one.
"""

from __future__ import annotations

import logging

import numpy as np
import pytest

import ada
from ada.api.containers import Nodes
from ada.api.mesh.containers import ArrayElements, to_array_backed
from ada.api.mesh.proxies import NodeProxy
from ada.config import logger
from ada.fem import FEM, Elem, FemSet, Mass, Spring
from ada.fem.containers import FemElements
from ada.fem.formats.sesam.read import cards
from ada.fem.formats.sesam.read.read_elements import lower_matrix
from ada.fem.formats.sesam.write.write_point_elements import point_elements_str
from ada.fem.formats.sesam.write.writer import node_dofs
from ada.fem.shapes.definitions import MassTypes, SolidShapes

PATHS = ["object", "array"]

# The node the mass sits on: a top-face corner of the hex, so the "solid-only node" NDOF
# rule below has a 3-dof node to put it on.
MASS_NODE = 5


def _hex_nodes() -> list[ada.Node]:
    """Eight nodes of a unit HEX8, bottom face (1-4) then top face (5-8)."""
    corners = [(0, 0), (1, 0), (1, 1), (0, 1)]
    return [
        ada.Node((x, y, z), 1 + i + 4 * layer) for layer, z in enumerate((0.0, 1.0)) for i, (x, y) in enumerate(corners)
    ]


def _solid_fem() -> FEM:
    nodes = _hex_nodes()
    return FEM("s", nodes=Nodes(nodes), elements=FemElements([Elem(1, nodes, SolidShapes.HEX8)]))


def _mass_fem(mass_value, mass_type, path: str) -> FEM:
    fem = _solid_fem()
    fs = fem.add_set(FemSet("ms", [fem.nodes.from_id(MASS_NODE)], FemSet.TYPES.NSET))
    fem.add_mass(Mass("m1", fs, mass_value, mass_type, parent=fem))
    if path == "array":
        to_array_backed(fem)
    return fem


def _mgmass(text: str) -> list[tuple[int, list[float]]]:
    """[(NDOF, matrix diagonal)] parsed back out of the MGMASS records (a point mass is a mass
    element with an MGMASS matrix; see ``write_point_elements``)."""
    return [(k.shape[0], np.diag(k).tolist()) for _, k in (lower_matrix(m) for m in cards.re_mgmass.finditer(text))]


@pytest.fixture
def warnings_visible(monkeypatch, caplog):
    """``configure_logger`` turns propagation off, so ``caplog`` -- which listens on the
    root logger -- sees nothing from adapy unless propagation is switched back on."""
    monkeypatch.setattr(logger, "propagate", True)
    with caplog.at_level(logging.WARNING, logger=logger.name):
        yield caplog


# ── the defect ───────────────────────────────────────────────────────────────────


def test_point_mass_writes_mgmass_on_the_object_path():
    """Today's good behaviour, pinned first so the array-path assertions below have
    something to be equal to."""
    fem = _mass_fem(12.0, MassTypes.MASS, "object")

    assert _mgmass(point_elements_str(fem, node_dofs(fem))) == [(3, [12.0, 12.0, 12.0])]


@pytest.mark.parametrize("path", PATHS)
def test_point_mass_writes_the_same_mgmass_after_packing(path):
    """The reported defect: on ``array`` this used to be the empty string."""
    fem = _mass_fem(12.0, MassTypes.MASS, path)

    assert _mgmass(point_elements_str(fem, node_dofs(fem))) == [(3, [12.0, 12.0, 12.0])]


@pytest.mark.parametrize("path", PATHS)
def test_rotary_inertia_writes_the_same_mgmass_after_packing(path):
    """ROTARYI is the case that also drives the node to 6 dofs, so it exercises both the
    mass values and the NDOF field they have to agree with."""
    fem = _mass_fem([1.0, 2.0, 3.0], MassTypes.ROTARYI, path)

    assert node_dofs(fem).ndof(MASS_NODE) == 6
    assert _mgmass(point_elements_str(fem, node_dofs(fem))) == [(6, [0.0, 0.0, 0.0, 1.0, 2.0, 3.0])]


def test_mass_records_are_byte_identical_across_the_two_paths():
    """Not just equal values -- the same bytes, for both mass kinds. ``to_array_backed``
    must be invisible in the deck."""
    for value, mass_type in ((12.0, MassTypes.MASS), ([1.0, 2.0, 3.0], MassTypes.ROTARYI)):
        obj = _mass_fem(value, mass_type, "object")
        arr = _mass_fem(value, mass_type, "array")
        assert point_elements_str(arr, node_dofs(arr)) == point_elements_str(obj, node_dofs(obj)) != ""


# ── what survives the packing ────────────────────────────────────────────────────


def test_packed_mass_keeps_its_values_name_and_type():
    """The values are the part a block cannot hold, so assert them off the object the
    array container hands back, not just off the deck text."""
    fem = _mass_fem([1.0, 2.0, 3.0], MassTypes.ROTARYI, "array")

    (mass,) = list(fem.elements.masses)
    assert isinstance(mass, Mass)
    assert mass.name == "m1"
    assert mass.type == MassTypes.ROTARYI
    assert mass._mass == [1.0, 2.0, 3.0]
    assert [m.id for m in mass.members] == [MASS_NODE]


def test_packed_mass_nodes_are_rebound_to_the_substrate():
    """The object Nodes it was built with are dropped by the conversion. Holding them
    would pin the whole object mesh (``Node.refs`` reaches every element that touches it)
    and freeze the coordinates, since ``ArrayNodes.move`` writes the arrays only."""
    fem = _mass_fem(12.0, MassTypes.MASS, "array")

    (mass,) = list(fem.elements.masses)
    (member,) = mass.members
    assert isinstance(member, NodeProxy)

    fem.nodes.move(move=(0.0, 0.0, 10.0))
    assert member.p[2] == pytest.approx(11.0)


def test_packing_leaves_element_iteration_and_grouping_alone():
    """The mass row stays in its block: the fix is additive. Anything that walks the
    store -- Sesam GELMNT, the Code_Aster MED mass group, the per-node NDOF derivation --
    must see exactly what it saw before."""
    obj = _mass_fem(12.0, MassTypes.MASS, "object")
    arr = _mass_fem(12.0, MassTypes.MASS, "array")

    assert isinstance(arr.elements, ArrayElements)
    assert len(arr.elements) == len(obj.elements) == 2
    assert {str(t): sorted(e.id for e in g) for t, g in arr.elements.group_by_type()} == {
        str(t): sorted(e.id for e in g) for t, g in obj.elements.group_by_type()
    }


def test_translational_mass_still_leaves_a_solid_only_node_at_3_dofs():
    """The packed block is still what ``node_dofs`` reads, so the rule that a plain point
    mass does not promote a solid-only node is unchanged by the fix."""
    fem = _mass_fem(12.0, MassTypes.MASS, "array")

    assert [node_dofs(fem).ndof(nid) for nid in range(1, 9)] == [3] * 8


def test_abaqus_and_usfos_get_the_same_mass_after_packing():
    """The other formats read the same ``fem.elements.masses``; they were losing the mass
    for the same reason and must now agree with their object-path output."""
    from ada.fem.formats.abaqus.write.write_masses import masses_str
    from ada.fem.formats.usfos.write.writer import mass_str as usfos_mass_str

    obj = _mass_fem(12.0, MassTypes.MASS, "object")
    arr = _mass_fem(12.0, MassTypes.MASS, "array")

    assert masses_str(arr, False) == masses_str(obj, False)
    assert "*Mass" in masses_str(arr, False)
    assert usfos_mass_str(arr) == usfos_mass_str(obj)
    assert f"NODEMASS       {MASS_NODE}" in usfos_mass_str(arr)


# ── springs and connectors had the same hole ──────────────────────────────────────


def test_spring_survives_packing():
    """``ArrayElements.springs`` read ``_overflow`` too, so a spring packed by
    ``to_array_backed`` disappeared along with its stiffness matrix."""
    fem = _solid_fem()
    fs = FemSet("spr_set", [fem.nodes.from_id(1)], FemSet.TYPES.NSET, parent=fem)
    stiff = np.diag([1e5, 2e5, 3e5, 4e5, 5e5, 6e5]).astype(float)
    fem.add_spring(Spring("spr1", 9001, "SPRING1", fem_set=fs, stiff=stiff, parent=fem))
    to_array_backed(fem)

    (spring,) = list(fem.elements.springs)
    assert isinstance(spring, Spring)
    assert np.allclose(spring.stiff, stiff)
    assert isinstance(spring.nodes[0], NodeProxy)


# ── the remaining hole is loud, not silent ────────────────────────────────────────


def test_a_mass_block_with_no_object_behind_it_warns(warnings_visible):
    """A store can still be assembled without the objects -- ``ada.fem.concat`` merges
    blocks and carries no ``_packed_specials`` across. Nothing can reconstruct the values
    from such a block, so the count is reported instead of the deck quietly losing it."""
    fem = _mass_fem(12.0, MassTypes.MASS, "array")
    fem.elements._packed_specials.clear()  # what a block-only merge leaves behind

    assert list(fem.elements.masses) == []
    assert point_elements_str(fem, node_dofs(fem)) == ""
    assert "1 packed mass/spring/connector element(s) carry no Mass" in warnings_visible.text


def test_no_warning_when_every_packed_special_is_accounted_for(warnings_visible):
    fem = _mass_fem(12.0, MassTypes.MASS, "array")

    assert len(list(fem.elements.masses)) == 1
    assert "carry no Mass" not in warnings_visible.text
