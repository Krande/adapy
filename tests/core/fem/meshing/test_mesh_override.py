"""`mesh_override`: per-object meshing rules, applied in whichever session meshes the object.

The override runs after the objects are added and partitioned against each other, and before the one `mesh` call
that meshes them all, so an overridden object conforms to its neighbours.
"""

import numpy as np
import pytest

import ada
from ada.base.types import GeomRepr
from ada.fem.meshing.overrides import element_size, refine_near_booleans


class Recorder:
    """An override that only records how it was called."""

    def __init__(self):
        self.calls: list = []

    def __call__(self, gs, obj, geom_repr):
        self.calls.append((obj.name, geom_repr, obj in gs.model_map))


def plate(name: str, x0: float = 0.0, width: float = 1.0) -> ada.Plate:
    return ada.Plate(name, [(x0, 0), (x0 + width, 0), (x0 + width, 1), (x0, 1)], 0.01)


def plate_with_hole(name: str = "pl1") -> ada.Plate:
    pl = plate(name, width=2.0)
    pl.add_boolean(ada.PrimCyl("hole", (1.0, 0.5, -0.1), (1.0, 0.5, 0.1), 0.2))
    return pl


def nodes_shared_on_line(fem, on_line, owners: set[str]) -> tuple[int, int]:
    """(nodes on the line, how many of them are used by elements of every object in `owners`)."""
    line = [n for n in fem.nodes if on_line(n.p)]
    by_node: dict[int, set[str]] = {}
    for el in fem.elements:
        if not el.refs:
            continue
        for n in el.nodes:
            by_node.setdefault(n.id, set()).add(el.refs[0].name)
    shared = sum(1 for n in line if owners <= by_node.get(n.id, set()))
    return len(line), shared


# --- the attribute ------------------------------------------------------------------------------


def test_no_override_by_default():
    assert plate("pl1").mesh_override is None
    assert ada.Beam("bm", (0, 0, 0), (1, 0, 0), "IPE300").mesh_override is None


def test_an_override_is_per_object():
    a, b = plate("a"), plate("b")
    a.mesh_override = Recorder()
    assert b.mesh_override is None


# --- the contract, through to_fem_obj -----------------------------------------------------------


def test_the_override_runs_once_on_a_session_holding_the_object():
    pl = plate("pl1")
    pl.mesh_override = Recorder()

    pl.to_fem_obj(0.5, "shell")

    assert pl.mesh_override.calls == [("pl1", GeomRepr.SHELL, True)]


def test_the_override_sees_the_size_the_mesh_is_made_at():
    """Overrides size relative to the mesh size, so it must be set before they run, not only when `mesh` runs."""
    seen = []
    pl = plate("pl1")
    pl.mesh_override = lambda gs, obj, geom_repr: seen.append(gs.options.Mesh_MeshSizeMax)

    pl.to_fem_obj(0.37, "shell")
    (ada.Part("p") / [plate("pl2")]).to_fem_obj(0.23, pl_repr="shell")  # no override: nothing recorded
    part_plate = plate("pl3")
    part_plate.mesh_override = pl.mesh_override
    (ada.Part("p2") / [part_plate]).to_fem_obj(0.23, pl_repr="shell")

    assert seen == [0.37, 0.23]


def test_part_use_mesh_override_false_ignores_the_overrides():
    left, right = plate("left"), plate("right", x0=1.0)
    right.mesh_override = Recorder()

    (ada.Part("p") / [left, right]).to_fem_obj(0.5, pl_repr="shell", use_mesh_override=False)

    assert right.mesh_override.calls == []


def test_an_override_that_returns_something_is_named():
    """Rules only: the session meshes. Returning a mesh is the old contract and is refused, by name."""
    pl = plate("pl1")
    pl.mesh_override = lambda gs, obj, geom_repr: 42

    with pytest.raises(TypeError, match="pl1"):
        pl.to_fem_obj(0.1, "shell")


def test_use_mesh_override_false_ignores_the_override():
    pl = plate("pl1")
    pl.mesh_override = Recorder()

    pl.to_fem_obj(0.5, "shell", use_mesh_override=False)

    assert pl.mesh_override.calls == []


def test_an_override_can_set_element_options():
    def second_order(gs, obj, geom_repr):
        gs.options.Mesh_ElementOrder = 2

    pl = plate("pl1")
    default = pl.to_fem_obj(0.5, "shell", use_quads=True)
    pl.mesh_override = second_order
    overridden = pl.to_fem_obj(0.5, "shell", use_quads=True)

    assert {len(el.nodes) for el in default.elements} == {4}
    assert {len(el.nodes) for el in overridden.elements} == {8}


def test_element_size_meshes_the_object_finer_than_the_call():
    pl = plate("pl1")
    default = pl.to_fem_obj(0.25, "shell")
    pl.mesh_override = element_size(0.05)
    overridden = pl.to_fem_obj(0.25, "shell")

    assert len(overridden.elements) > 10 * len(default.elements)


def test_skip_default_meshing_leaves_the_objects_structure_to_its_override():
    """With quads on, the default makes the plate transfinite at the call's size (2 x 2 at 0.5). An override that
    sets its own 4 x 4 transfinite grid and opts out gets exactly that."""

    def grid_4x4(gs, obj, geom_repr):
        surfaces = [tag for dim, tag in gs.model_map[obj].entities if dim == 2]
        for _, curve in gs.model.getBoundary([(2, s) for s in surfaces], combined=False, oriented=False):
            gs.model.mesh.setTransfiniteCurve(abs(curve), 5)
        for s in surfaces:
            gs.model.mesh.setTransfiniteSurface(s)
            gs.model.mesh.setRecombine(2, s)
        gs.skip_default_meshing(obj)

    pl = plate("pl1")
    assert len(pl.to_fem_obj(0.5, "shell", use_quads=True).elements) == 4
    pl.mesh_override = grid_4x4
    assert len(pl.to_fem_obj(0.5, "shell", use_quads=True).elements) == 16


def test_beam_override_keeps_the_beams_concept_handling():
    bm = ada.Beam("bm1", (0, 0, 0), (1, 0, 0), "IPE300")
    bm.mesh_override = Recorder()
    assert len(bm.to_fem_obj(0.5, "line").elements) == 2


# --- refine_near_booleans -------------------------------------------------------------------------


def test_refine_near_booleans_refines_around_the_hole():
    uniform = plate_with_hole().to_fem_obj(0.1, "shell", use_quads=True)

    pl = plate_with_hole()
    pl.mesh_override = refine_near_booleans(size=0.02, dist_max=0.3)
    refined = pl.to_fem_obj(0.1, "shell", use_quads=True)

    assert len(refined.elements) > 2 * len(uniform.elements)


def test_refine_near_booleans_puts_the_small_elements_at_the_hole():
    """The refinement is local: elements next to the hole are much smaller than those far from it."""
    pl = plate_with_hole()
    pl.mesh_override = refine_near_booleans(size=0.02, dist_max=0.3, max_size=0.1)
    fem = pl.to_fem_obj(0.1, "shell")

    def edge(el) -> float:
        p = np.array([n.p for n in el.nodes])
        return float(np.max(np.linalg.norm(p - np.roll(p, 1, axis=0), axis=1)))

    def dist_to_hole(el) -> float:
        c = np.mean([n.p for n in el.nodes], axis=0)
        return float(np.hypot(c[0] - 1.0, c[1] - 0.5) - 0.2)

    near = [edge(el) for el in fem.elements if dist_to_hole(el) < 0.03]
    far = [edge(el) for el in fem.elements if dist_to_hole(el) > 0.4]
    assert near and far
    assert np.median(near) < 0.5 * np.median(far)


def test_refine_near_booleans_leaves_an_object_without_booleans_to_the_defaults():
    default = plate("pl1").to_fem_obj(0.25, "shell", use_quads=True)
    pl = plate("pl1")
    pl.mesh_override = refine_near_booleans(size=0.05, dist_max=0.3)
    assert len(pl.to_fem_obj(0.25, "shell", use_quads=True).elements) == len(default.elements)


def test_refine_near_booleans_on_a_beam_web_hole():
    bm = ada.Beam("bm", (0, 0, 0), (2, 0, 0), "IPE400")
    bm.add_boolean(ada.PrimCyl("web_hole", (1.0, -0.5, 0.0), (1.0, 0.5, 0.0), 0.1))
    uniform = bm.to_fem_obj(0.05, "shell", use_quads=True)

    bm.mesh_override = refine_near_booleans(size=0.01, dist_max=0.15)
    refined = bm.to_fem_obj(0.05, "shell", use_quads=True)

    assert len(refined.elements) > len(uniform.elements)


@pytest.mark.parametrize("size, max_size", [(0, 0.1), (-0.1, 0.1), (0.2, 0.1)])
def test_refine_near_booleans_refuses_inconsistent_sizes(size, max_size):
    with pytest.raises(ValueError):
        refine_near_booleans(size=size, dist_max=0.3, max_size=max_size)


def test_element_size_refuses_a_non_positive_size():
    with pytest.raises(ValueError):
        element_size(0)


# --- in a part: conformity with neighbours ------------------------------------------------------


def _girder_through_deck(override_on: str | None):
    """A shell IPE300 girder along y=0.5 whose web crosses a shell deck at z=0: partitioning splits the deck along
    that line, so deck and web must share every node on it."""
    deck = plate("deck", width=2.0)
    girder = ada.Beam("girder", (0, 0.5, 0), (2, 0.5, 0), "IPE300")
    if override_on == "girder":
        girder.mesh_override = element_size(0.025)
    elif override_on == "deck":
        deck.mesh_override = element_size(0.025)
    fem = (ada.Part("p") / [deck, girder]).to_fem_obj(0.1, bm_repr="shell", pl_repr="shell")
    return fem, deck, girder


def _on_girder_line(p) -> bool:
    return abs(p[1] - 0.5) < 1e-6 and abs(p[2]) < 1e-6


@pytest.mark.parametrize("override_on", [None, "girder", "deck"])
def test_a_shell_girder_and_the_deck_it_crosses_share_their_nodes(override_on):
    fem, _, _ = _girder_through_deck(override_on)

    n_line, shared = nodes_shared_on_line(fem, _on_girder_line, {"deck", "girder"})

    assert n_line > 0
    assert shared == n_line


def test_refining_the_girder_grades_the_deck_to_meet_it():
    _, deck_default, girder_default = _girder_through_deck(None)
    fem, deck, girder = _girder_through_deck("girder")

    assert len(girder.elem_refs) > 4 * len(girder_default.elem_refs)
    # The deck is refined only towards the shared line, so it grows, but far less than the girder.
    assert len(deck_default.elem_refs) < len(deck.elem_refs) < len(girder.elem_refs)
    # The line itself is seeded at the girder's size on both sides.
    n_line, shared = nodes_shared_on_line(fem, _on_girder_line, {"deck", "girder"})
    assert shared == n_line == 81


def test_a_refined_hole_next_to_a_perpendicular_plate_keeps_their_shared_edge():
    """A deck and a bulkhead meeting along y=0; the bulkhead has a hole near that edge, refined by its override."""
    deck = ada.Plate("deck", [(0, 0), (2, 0), (2, 1), (0, 1)], 0.01)
    bulkhead = ada.Plate.from_3d_points("bulkhead", [(0, 0, 0), (2, 0, 0), (2, 0, 1), (0, 0, 1)], 0.01)
    bulkhead.add_boolean(ada.PrimCyl("hole", (1.0, -0.1, 0.3), (1.0, 0.1, 0.3), 0.15))
    bulkhead.mesh_override = refine_near_booleans(size=0.02, dist_max=0.25)

    fem = (ada.Part("p") / [deck, bulkhead]).to_fem_obj(0.1, pl_repr="shell")

    n_edge, shared = nodes_shared_on_line(fem, lambda p: abs(p[1]) < 1e-6 and abs(p[2]) < 1e-6, {"deck", "bulkhead"})
    assert n_edge > 0
    assert shared == n_edge


def test_overrides_on_two_objects_compose():
    """Each override registers its own size field; the session combines them, so neither replaces the other."""

    def build(on_left: bool, on_right: bool):
        left = plate_with_hole("left")
        right = ada.Plate("right", [(0, 2), (2, 2), (2, 3), (0, 3)], 0.01)
        right.add_boolean(ada.PrimCyl("hole_r", (1.0, 2.5, -0.1), (1.0, 2.5, 0.1), 0.2))
        if on_left:
            left.mesh_override = refine_near_booleans(size=0.02, dist_max=0.3)
        if on_right:
            right.mesh_override = refine_near_booleans(size=0.02, dist_max=0.3)
        (ada.Part("p") / [left, right]).to_fem_obj(0.1, pl_repr="shell")
        return len(left.elem_refs), len(right.elem_refs)

    only_left = build(True, False)
    only_right = build(False, True)
    both = build(True, True)

    assert both[0] == pytest.approx(only_left[0], rel=0.05)
    assert both[1] == pytest.approx(only_right[1], rel=0.05)
    assert both[0] > 2 * only_right[0]  # the left plate is refined only when its own override is set


def test_part_passes_each_kinds_representation():
    bm = ada.Beam("bm1", (0, 0, 0), (1, 0, 0), "IPE300")
    box = ada.PrimBox("box", (0, 2, 0), (1, 3, 1))
    bm.mesh_override, box.mesh_override = Recorder(), Recorder()

    (ada.Part("p") / [bm, box]).to_fem_obj(0.5, bm_repr="line", shp_repr="solid")

    assert [c[1] for c in bm.mesh_override.calls] == [GeomRepr.LINE]
    assert [c[1] for c in box.mesh_override.calls] == [GeomRepr.SOLID]


def test_a_mass_shape_is_not_meshed_so_its_override_is_not_called():
    from ada.fem.elements import Mass

    box = ada.PrimBox("m", (0, 0, 0), (1, 1, 1), mass=100.0)
    box.mesh_override = Recorder()

    fem = (ada.Part("p") / [box, plate("pl1")]).to_fem_obj(0.5, pl_repr="shell", use_quads=True)

    assert box.mesh_override.calls == []
    assert len([el for el in fem.elements if isinstance(el, Mass)]) == 1
