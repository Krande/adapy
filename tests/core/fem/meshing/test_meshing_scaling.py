"""The partitioning steps of ``Part.to_fem_obj`` answer "which objects could touch" from a grid instead of asking every
other object, and list the gmsh model once instead of once per object. These tests pin that the answers are the ones
the all-pairs versions give, and that the model is listed once."""

import numpy as np
import pytest

import ada
from ada.core import clash_check
from ada.core.spatial_grid import AabbGrid


def _stiffened_box(nx=3, ny=2, a=3.0, b=2.8, h=3.0) -> ada.Assembly:
    """A deck of nx x ny stiffened panels on girders, and a second deck in a moved and rotated part, with walls."""
    asm = ada.Assembly("Box")
    for lv, placement in enumerate(
        [ada.Placement(), ada.Placement(origin=(2.0, -1.0, h), xdir=(0, 1, 0), zdir=(0, 0, 1))]
    ):
        part = ada.Part(f"Deck{lv}", placement=placement)
        asm.add_part(part)
        for i in range(nx):
            for j in range(ny):
                x0, y0 = i * a, j * b
                pts = [(x0, y0, 0), (x0 + a, y0, 0), (x0 + a, y0 + b, 0), (x0, y0 + b, 0)]
                part.add_plate(ada.Plate.from_3d_points(f"pl{lv}_{i}_{j}", pts, 0.012))
                for k in (1, 2):
                    y = y0 + k * b / 3
                    part.add_beam(ada.Beam(f"st{lv}_{i}_{j}_{k}", (x0, y, 0), (x0 + a, y, 0), "HP140x8"))
        for i in range(nx):
            for j in range(ny + 1):
                part.add_beam(ada.Beam(f"gx{lv}_{i}_{j}", (i * a, j * b, 0), ((i + 1) * a, j * b, 0), "IPE300"))
        for i in range(nx + 1):
            for j in range(ny):
                part.add_beam(ada.Beam(f"gy{lv}_{i}_{j}", (i * a, j * b, 0), (i * a, (j + 1) * b, 0), "IPE300"))
        for i in range(nx):
            pts = [(i * a, 0, 0), ((i + 1) * a, 0, 0), ((i + 1) * a, 0, h), (i * a, 0, h)]
            part.add_plate(ada.Plate.from_3d_points(f"wl{lv}_{i}", pts, 0.010))
        # a wall standing on the deck mid-span: a mid-span (T) connection
        pts = [(a / 2, 0, 0), (a / 2, b, 0), (a / 2, b, h / 2), (a / 2, 0, h / 2)]
        part.add_plate(ada.Plate.from_3d_points(f"mid{lv}", pts, 0.010))
    return asm


def test_grid_candidates_hold_every_overlapping_box():
    rng = np.random.default_rng(7)
    n = 400
    lo = rng.uniform(-20, 20, (n, 3))
    hi = lo + rng.exponential(1.5, (n, 3))
    hi[:5] = lo[:5] + 200.0  # boxes covering most of the model are offered to every query
    lo[5], hi[5] = -np.inf, np.inf  # as is a box without bounds
    grid = AabbGrid(lo, hi)
    for _ in range(200):
        qlo = rng.uniform(-25, 25, 3)
        qhi = qlo + rng.exponential(2.0, 3)
        overlap = np.flatnonzero(np.all((lo <= qhi) & (qlo <= hi), axis=1))
        cand = grid.candidates(qlo, qhi)
        assert set(overlap) <= set(cand)
        assert np.all(np.diff(cand) > 0)


def test_basic_intersect_all_is_basic_intersect_for_every_beam():
    from ada.api.containers import Beams

    beams = list(_stiffened_box().get_all_physical_objects(by_type=ada.Beam))
    cont = Beams(beams)
    expected = [
        (bm, list(cb)) for bm, cb in filter(None, [clash_check.basic_intersect(bm, 5e-5, [cont]) for bm in beams])
    ]
    got = clash_check.basic_intersect_all(beams, 5e-5, [cont])
    assert [(bm.name, [c.name for c in cb]) for bm, cb in got] == [
        (bm.name, [c.name for c in cb]) for bm, cb in expected
    ]
    assert sum(len(cb) for _, cb in got) > 0


def test_plate_boxes_hold_every_touching_pair():
    from ada.core.clash_distance import plates_min_distance

    plates = list(_stiffened_box().get_all_physical_objects(by_type=ada.Plate))
    lo, hi = clash_check._plate_world_boxes(plates, pad=clash_check._PLATE_TOUCH_TOL)
    touching = 0
    for i, p1 in enumerate(plates):
        for j, p2 in enumerate(plates):
            if i < j and plates_min_distance(p1, p2) is not None:
                touching += 1
                assert np.all((lo[i] <= hi[j]) & (lo[j] <= hi[i])), (p1.name, p2.name)
    assert touching > 0


def test_plate_connections_are_the_all_pairs_ones(monkeypatch):
    plates = list(_stiffened_box().get_all_physical_objects(by_type=ada.Plate))
    got = clash_check.find_edge_connected_perpendicular_plates(plates)

    # The all-pairs version: every plate offered to every plate.
    class AllPairs(AabbGrid):
        def candidates(self, qlo, qhi):
            return np.arange(self.n, dtype=np.int64)

    monkeypatch.setattr("ada.core.spatial_grid.AabbGrid", AllPairs)
    monkeypatch.setattr(
        clash_check,
        "_plate_world_boxes",
        lambda pls, pad: (np.full((len(pls), 3), -np.inf), np.full((len(pls), 3), np.inf)),
    )
    expected = clash_check.find_edge_connected_perpendicular_plates(plates)

    def names(mapping):
        return [(k.name, [v.name for v in vals]) for k, vals in mapping.items()]

    assert names(got.edge_connected) == names(expected.edge_connected)
    assert names(got.mid_span_connected) == names(expected.mid_span_connected)
    assert got.edge_connected and got.mid_span_connected


def test_beams_on_plate_with_a_shared_index_are_the_same():
    asm = _stiffened_box()
    beams = list(asm.get_all_physical_objects(by_type=ada.Beam))
    index = clash_check.BeamMidspanIndex(beams)
    for pl in asm.get_all_physical_objects(by_type=ada.Plate):
        assert clash_check.find_beams_connected_to_plate(pl, beams, index=index) == (
            clash_check.find_beams_connected_to_plate(pl, beams)
        )


def test_model_entities_are_listed_once_per_check(monkeypatch):
    import gmsh

    from ada.fem.meshing import GmshSession

    asm = _stiffened_box(nx=2, ny=1)
    calls = []
    with GmshSession(silent=True) as gs:
        for obj in asm.get_all_physical_objects(by_type=ada.Plate):
            gs.add_obj(obj, geom_repr="shell", synchronize=False)
        gs.synchronize()
        real = gmsh.model.occ.getEntities
        monkeypatch.setattr(gmsh.model.occ, "getEntities", lambda dim=-1: calls.append(dim) or real(dim))
        gs.check_model_entities()
    assert len(calls) == 1


@pytest.mark.parametrize("use_quads", [False, True])
def test_mesh_is_the_same_with_one_synchronisation(monkeypatch, use_quads):
    """``Part.to_fem_obj`` adds every object without synchronising and synchronises once; the mesh is the one a
    synchronisation per object gives."""
    from ada.fem.meshing import GmshSession

    def fingerprint(fem):
        elems = sorted((str(e.type), tuple(sorted(tuple(np.round(n.p, 9)) for n in e.nodes))) for e in fem.elements)
        sets = sorted((s.name, len(s.members)) for s in fem.sets)
        return len(fem.nodes), elems, sets

    asm = _stiffened_box(nx=2, ny=1)
    fast = fingerprint(asm.to_fem_obj(0.5, use_quads=use_quads))

    add_obj = GmshSession.add_obj

    def add_obj_synchronised(self, *args, **kwargs):
        kwargs["synchronize"] = True
        return add_obj(self, *args, **kwargs)

    monkeypatch.setattr(GmshSession, "add_obj", add_obj_synchronised)
    slow = fingerprint(_stiffened_box(nx=2, ny=1).to_fem_obj(0.5, use_quads=use_quads))
    assert fast == slow


def _crossing_part() -> ada.Part:
    """One deck plate cut by stiffeners in both directions -- every face a split leaves is split again by each crossing
    stiffener -- with a wall standing on it at mid-span across two of them, and a solid box beside it."""
    p = ada.Part("X")
    p.add_plate(ada.Plate.from_3d_points("deck", [(0, 0, 0), (4, 0, 0), (4, 3, 0), (0, 3, 0)], 0.010))
    for k, x in enumerate((1.0, 2.0, 3.0)):
        p.add_beam(ada.Beam(f"sy{k}", (x, 0, 0), (x, 3, 0), "IPE200"))
    for k, y in enumerate((1.0, 2.0)):
        p.add_beam(ada.Beam(f"sx{k}", (0, y, 0), (4, y, 0), "IPE200"))
    wall = [(1.5, 0, 0), (1.5, 3, 0), (1.5, 3, 1.5), (1.5, 0, 1.5)]
    p.add_plate(ada.Plate.from_3d_points("wall", wall, 0.010))
    p.add_shape(ada.PrimBox("box", (5, 0, 0), (6, 1, 1)))
    return p


def _mesh_fingerprint(fem):
    elems = sorted((str(e.type), tuple(sorted(tuple(np.round(n.p, 9)) for n in e.nodes))) for e in fem.elements)
    sets = sorted((s.name, len(s.members)) for s in fem.sets)
    return len(fem.nodes), elems, sets


@pytest.mark.parametrize("use_quads", [False, True])
def test_repeated_splits_need_no_synchronisation_between_them(monkeypatch, use_quads):
    """A plate split, then split again where it was split (crossing stiffeners, a wall across them): one
    synchronisation after all objects are added gives the mesh that synchronising after every added object, and after
    every ``occ.fragment`` of the partitioning, gives. The fragments work on the OCC kernel and hand the new tags back
    (``res_map``), so none of them reads gmsh's model, which is what a synchronisation brings up to date."""
    import gmsh

    from ada.fem.meshing import GmshSession

    fem = _crossing_part().to_fem_obj(0.25, use_quads=use_quads)
    fast = _mesh_fingerprint(fem)

    # conformal: every beam node is a node of the shells, so each stiffener did split the deck where it lies
    shell_nodes = {tuple(np.round(n.p, 9)) for el in fem.elements.shell for n in el.nodes}
    beam_nodes = {tuple(np.round(n.p, 9)) for el in fem.elements.lines for n in el.nodes}
    assert beam_nodes and beam_nodes <= shell_nodes
    assert len(list(fem.elements.solids)) > 0

    add_obj = GmshSession.add_obj

    def add_obj_synchronised(self, *args, **kwargs):
        kwargs["synchronize"] = True
        return add_obj(self, *args, **kwargs)

    fragment = gmsh.model.occ.fragment

    def fragment_synchronised(*args, **kwargs):
        out = fragment(*args, **kwargs)
        gmsh.model.occ.synchronize()
        return out

    monkeypatch.setattr(GmshSession, "add_obj", add_obj_synchronised)
    assert _mesh_fingerprint(_crossing_part().to_fem_obj(0.25, use_quads=use_quads)) == fast

    monkeypatch.setattr(gmsh.model.occ, "fragment", fragment_synchronised)
    assert _mesh_fingerprint(_crossing_part().to_fem_obj(0.25, use_quads=use_quads)) == fast
