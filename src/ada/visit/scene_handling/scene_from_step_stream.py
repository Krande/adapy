from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import trimesh

    from ada.visit.scene_converter import SceneConverter


@dataclass
class StepStreamSource:
    """Marks a :class:`SceneConverter` source as a STEP file to convert by *streaming*
    — one solid at a time, bounded memory — instead of loading the whole Assembly.

    The reader yields one geometry per solid (with its STEP colour); each is
    tessellated and its light mesh buffers are accumulated per material, while the
    heavy B-rep geometry is dropped before the next solid. The result is the same
    merged-by-colour scene + design-graph the normal ``to_gltf`` path produces.
    """

    path: str | Path
    tolerant: bool = True


def scene_from_step_stream(source: StepStreamSource, converter: SceneConverter) -> trimesh.Scene:
    import collections

    import trimesh

    from ada.cad import active_backend
    from ada.cadit.step.read.stream_reader import stream_read_step
    from ada.config import logger
    from ada.core.guid import create_guid
    from ada.occ.tessellating import BatchTessellator
    from ada.visit.gltf.graph import GraphNode
    from ada.visit.gltf.meshes import MeshType
    from ada.visit.gltf.optimize import concatenate_stores
    from ada.visit.gltf.store import merged_mesh_to_trimesh_scene

    bt = BatchTessellator()
    params = converter.params
    graph = converter.graph
    root = graph.top_level
    scene = trimesh.Scene(base_frame=root.name)
    be = active_backend()

    # mat_id -> [MeshStore]. Mesh buffers are flat float/int arrays (a few tens of MB
    # for a 100k-triangle model), so accumulating them per material — then merging once
    # at the end — keeps memory bounded; the B-rep geometry is freed each iteration.
    by_material: dict[int, list] = collections.defaultdict(list)
    reasons: collections.Counter = collections.Counter()
    skipped_ids: list[str] = []
    n_total = 0

    def _skip(gid: str, reason: str) -> None:
        reasons[reason] += 1
        if len(skipped_ids) < 50:
            skipped_ids.append(gid)
        logger.debug("scene_from_step_stream: skipped %s — %s", gid, reason)

    for i, geom in enumerate(stream_read_step(source.path, local_pool=False, tolerant=source.tolerant)):
        n_total += 1
        gid = str(geom.id) if geom.id not in (None, "") else f"solid_{i}"
        try:
            occ = be.build(geom)
            # A zero-extent solid makes OCC's relative mesher throw an uncatchable
            # "deviation must be greater than 0" terminate — skip it via the bbox.
            try:
                bb = be.bbox(occ)
                diag = ((bb[3] - bb[0]) ** 2 + (bb[4] - bb[1]) ** 2 + (bb[5] - bb[2]) ** 2) ** 0.5
            except Exception:
                diag = 0.0
            if diag < 1e-7:
                _skip(gid, "degenerate (zero-extent solid)")
                continue
            node = graph.add_node(GraphNode(gid, graph.next_node_id(), hash=create_guid(), parent=root))
            ms = bt.tessellate_occ_geom(occ, node.hash, geom.color, MeshType.TRIANGLES)
            if ms.indices is None or len(ms.indices) == 0:
                _skip(gid, "empty mesh (no triangles)")
                continue
            by_material[ms.material].append(ms)
        except Exception as exc:  # noqa: BLE001 - one bad solid must not abort the file
            _skip(gid, f"{type(exc).__name__}: {str(exc)[:100]}")
        # occ / geom become unreferenced here and are freed before the next solid.

    # One merged mesh (glTF node) per material/colour — the default GLB shape.
    for mat_id, stores in by_material.items():
        merged = concatenate_stores(stores, graph)
        if merged is None:
            continue
        merged_mesh_to_trimesh_scene(
            scene, merged, bt.get_mat_by_id(mat_id), mat_id, graph, apply_transform=params.apply_transform
        )

    n_skipped = sum(reasons.values())
    if n_skipped:
        more = f" (+{n_skipped - len(skipped_ids)} more)" if n_skipped > len(skipped_ids) else ""
        logger.warning(
            "scene_from_step_stream: %s — skipped %d/%d solids by reason %s; ids: %s%s",
            source.path, n_skipped, n_total, dict(reasons), ", ".join(skipped_ids), more,
        )
    logger.info(
        "scene_from_step_stream: %s — meshed %d/%d solids into %d material group(s)",
        source.path, n_total - n_skipped, n_total, len(by_material),
    )
    scene.metadata["ada_stream_stats"] = {
        "meshed": n_total - n_skipped,
        "total": n_total,
        "skipped": n_skipped,
        "materials": len(by_material),
        "reasons": dict(reasons),
    }
    return scene
