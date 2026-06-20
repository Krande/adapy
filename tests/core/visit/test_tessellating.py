import os
from itertools import groupby

import trimesh

from ada.occ.tessellating import BatchTessellator
from ada.param_models.primitives_generators import ShapeGenerator
from ada.visit.gltf.optimize import concatenate_stores
from ada.visit.gltf.store import merged_mesh_to_trimesh_scene


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
    from ada.occ.tessellating import BatchTessellator

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


def test_tessellate_empty_shape_does_not_abort():
    """An empty body (void bbox) used to crash the process on *both* backends:
    OCC's ShapeTesselator derives a relative deflection from the bbox (0 →
    std::invalid_argument that escapes pythonocc), and adacpp's native
    tessellator throws an untranslatable nanobind exception. Empty bodies turn
    up from round-trips that drop geometry; ``tessellate_shape`` must return an
    empty mesh, not crash. Built through ``active_backend()`` (a box cut by
    itself → geometry-less shape) so it covers whichever backend is active."""
    from ada.cad import active_backend
    from ada.geom.booleans import BoolOpEnum
    from ada.occ.tessellating import tessellate_shape

    be = active_backend()
    empty = be.boolean(BoolOpEnum.DIFFERENCE, be.make_box(1.0, 1.0, 1.0), be.make_box(1.0, 1.0, 1.0))

    tm = tessellate_shape(empty)  # must not abort the interpreter
    assert len(tm.faces) == 0
    assert len(tm.positions) == 0
