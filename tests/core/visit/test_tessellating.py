import os
from itertools import groupby

import trimesh

from ada.param_models.primitives_generators import ShapeGenerator
from ada.visit.gltf.optimize import concatenate_stores
from ada.visit.gltf.store import merged_mesh_to_trimesh_scene
from ada.visit.tessellate import BatchTessellator


def test_shape_grid(tmp_path):
    bg = ShapeGenerator(grid_size=1)
    shape_grid = list(bg.generate_shape_grid())

    bt = BatchTessellator()
    all_shapes = sorted(bt.batch_tessellate(shape_grid), key=lambda x: x.material)

    scene = trimesh.Scene(base_frame=bg.graph.top_level.name)
    scene.metadata["meta"] = bg.graph.to_json_hierarchy(suffix="")
    mesh_map = []
    for mat_id, meshes in groupby(all_shapes, lambda x: x.material):
        meshes = list(meshes)
        merged_store = concatenate_stores(meshes)
        mesh_map.append((mat_id, meshes, merged_store))
        merged_mesh_to_trimesh_scene(scene, merged_store, bt.get_mat_by_id(mat_id), mat_id, bg.graph)

    # assert mat0
    assert len(mesh_map) == len(scene.geometry)
    mat_id0, meshes0, merged_store0 = mesh_map[0]
    scene.geometry.get(f"node{mat_id0}")
    os.makedirs(tmp_path, exist_ok=True)
    scene.export(tmp_path / "test.glb")


def test_tessellate_batch_combined_mesh():
    # The CadBackend.tessellate_batch abstraction returns one combined BatchMesh
    # whose per-shape GroupReferences slice the shared buffer to match the
    # individual tessellations. Works on whichever backend is active (native
    # batch under ada-cpp builds that support it, else the loop fallback).
    import numpy as np

    import ada
    from ada.cad import BatchMesh, active_backend

    b = active_backend()
    shapes = [b.build(ada.PrimBox(f"b{i}", (0, i, 0), (1, i + 1, 1)).solid_geom()) for i in range(6)]
    singles = [b.tessellate(s, 0.1) for s in shapes]

    bm = b.tessellate_batch(shapes, 0.1)
    assert isinstance(bm, BatchMesh)
    assert len(bm.groups) == len(shapes)

    def _idx_len(m):
        raw = getattr(m, "indices", None)
        return np.asarray(m.faces if raw is None else raw).size

    assert bm.indices.size == sum(_idx_len(s) for s in singles)
    cursor = 0
    for i, (g, s) in enumerate(zip(bm.groups, singles)):
        assert g.node_id == i
        assert g.start == cursor
        assert g.length == _idx_len(s)
        cursor += g.length
    # combined indices stay in range of the combined vertex buffer
    assert int(bm.indices.max()) < bm.positions.size // 3


def test_batch_tessellate_solids_matches_per_object():
    # The batched fast path (one backend call, split back per object via group
    # vertex ranges) must yield MeshStores equivalent to the per-object path,
    # preserving each object's colour/material so the downstream per-colour merge
    # is unchanged. Under pythonocc both paths go through the same tessellator, so
    # the meshes are identical.
    import numpy as np

    import ada
    from ada.visit.tessellate import BatchTessellator

    objs = [ada.PrimBox(f"b{i}", (0, i, 0), (1, i + 1, 1)) for i in range(3)] + [
        ada.Beam(f"bm{i}", (0, i, 2), (1, i, 2), "IPE300") for i in range(2)
    ]

    per = list(BatchTessellator().batch_tessellate(list(objs)))
    bat = list(BatchTessellator().batch_tessellate_solids(list(objs)))

    assert len(bat) == len(per)
    for a, b in zip(per, bat):
        assert np.allclose(np.asarray(a.position), np.asarray(b.position))
        assert np.array_equal(np.asarray(a.indices), np.asarray(b.indices))
        assert a.material == b.material
        assert b.normal is not None and len(b.normal) == len(b.position)


def _broken_box(name, p1, p2):
    """A box whose geometry stops building once it exists (its constructor builds it once)."""
    import ada

    box = ada.PrimBox(name, p1, p2)

    def solid_geom():
        raise ValueError("degenerate profile")

    box.solid_geom = solid_geom
    return box


def test_batch_tessellate_skips_and_records_failed_object():
    """One object whose geometry cannot be built must not sink the rest: it is skipped, and the
    active collector gets its identity and the error."""
    import ada
    from ada.visit.tessellate import BatchTessellator, collect_tessellation_failures

    part = ada.Part("part_a")
    good = [part.add_shape(ada.PrimBox(f"box_{i}", (0, i, 0), (1, i + 1, 1))) for i in range(2)]
    bad = part.add_shape(_broken_box("box_bad", (0, 5, 0), (1, 6, 1)))

    with collect_tessellation_failures() as failures:
        meshes = list(BatchTessellator().batch_tessellate([good[0], bad, good[1]]))

    assert len(meshes) == 2
    assert [(f.name, f.kind, f.stage, f.error_type) for f in failures] == [
        ("box_bad", "PrimBox", "geometry", "ValueError")
    ]
    assert failures[0].guid == bad.guid
    assert failures[0].path[-1] == "part_a"
    assert failures[0].to_dict()["error"] == "degenerate profile"


def test_part_to_gltf_survives_failed_object(tmp_path):
    import ada
    from ada.visit.tessellate import collect_tessellation_failures

    part = ada.Part("part_a")
    part.add_shape(ada.PrimBox("box_ok", (0, 0, 0), (1, 1, 1)))
    part.add_shape(_broken_box("box_bad", (0, 5, 0), (1, 6, 1)))
    assembly = ada.Assembly()
    assembly.add_part(part)

    with collect_tessellation_failures() as failures:
        assembly.to_gltf(tmp_path / "model.glb")

    assert (tmp_path / "model.glb").stat().st_size > 0
    assert [f.name for f in failures] == ["box_bad"]


def test_part_to_gltf_skips_profile_collapsed_by_tolerance(tmp_path):
    """A profile smaller than the point tolerance builds its curve lazily, at tessellation, and
    used to abort the whole GLB from there."""
    import ada
    from ada.visit.tessellate import collect_tessellation_failures

    part = ada.Part("part_a")
    part.add_shape(ada.PrimBox("box_ok", (0, 0, 0), (1, 1, 1)))
    tiny = [(0, 0), (0.0004, 0), (0.0004, 0.0004), (0, 0.0004)]
    part.add_shape(ada.PrimExtrude("extrude_tiny", tiny, 1.0, (0, 0, 1), (0, 0, 0), (1, 0, 0)))
    assembly = ada.Assembly()
    assembly.add_part(part)

    with collect_tessellation_failures() as failures:
        assembly.to_gltf(tmp_path / "model.glb")

    assert (tmp_path / "model.glb").stat().st_size > 0
    assert [(f.name, f.stage) for f in failures] == [("extrude_tiny", "geometry")]
    assert "collapsed" in failures[0].error


def test_tessellation_failures_outside_collector_are_only_logged():
    from ada.visit.tessellate import BatchTessellator, collect_tessellation_failures

    assert list(BatchTessellator().batch_tessellate([_broken_box("box_bad", (0, 0, 0), (1, 1, 1))])) == []
    with collect_tessellation_failures() as failures:
        pass
    assert failures == []


def test_tessellate_empty_shape_does_not_abort():
    """An empty body (void bbox) used to crash the process on *both* backends:
    OCC's ShapeTesselator derives a relative deflection from the bbox (0 â†’
    std::invalid_argument that escapes pythonocc), and adacpp's native
    tessellator throws an untranslatable nanobind exception. Empty bodies turn
    up from round-trips that drop geometry; ``tessellate_shape`` must return an
    empty mesh, not crash. Built through ``active_backend()`` (a box cut by
    itself â†’ geometry-less shape) so it covers whichever backend is active."""
    from ada.cad import active_backend
    from ada.geom.booleans import BoolOpEnum
    from ada.visit.tessellate import tessellate_shape

    be = active_backend()
    empty = be.boolean(BoolOpEnum.DIFFERENCE, be.make_box(1.0, 1.0, 1.0), be.make_box(1.0, 1.0, 1.0))

    tm = tessellate_shape(empty)  # must not abort the interpreter
    assert len(tm.faces) == 0
    assert len(tm.positions) == 0
