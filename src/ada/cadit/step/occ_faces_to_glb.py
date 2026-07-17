"""OCC-tessellated STEP->GLB with per-source-face clickable regions.

This is the OCC counterpart to the adacpp native ``face_regions`` path: it produces a
merge-by-colour GLB whose ``scenes[0].extras`` carries the SAME face-picking contract the
native writer emits, but where the triangles come from pythonocc's ``BRepMesh`` instead of
adacpp/libtess2. That lets the viewer compare OCC's meshing against native's face-by-face.

The heavy OCC work — OCAF read (colours/names) + per-``TopoDS_Face`` tessellation with
triangle->face tracking — lives entirely behind the CAD backend
(:meth:`ada.occ.backend.OccBackend.step_to_face_tagged_meshes`), resolved via
``ada.cad.active_backend``. This module only assembles the returned plain data into a GLB
using the backend-neutral ``ada.visit`` graph/spill utilities, so it never imports OCC.

Emitted ``scenes[0].extras`` (read by ``src/frontend/.../cacheModelUtils.ts``):

* ``id_hierarchy``           = ``{node_id: [name, parent_id]}``
* ``draw_ranges_node<m>``    = ``{node_id: [start, len]}``     — per material ``m``, per solid
* ``face_ranges_node<m>``    = ``{node_id: [[start, len, faceId, seq], ...]}``

``draw_ranges`` start/len are index-offsets into material ``m``'s merged buffer; each
``face_ranges`` ``start``/``len`` subdivides that node's draw-range and is **relative to
the draw-range start** (0-based within the node/material group). ``faceId`` is the STEP
``#NNNN`` entity id of the source ADVANCED_FACE — the SAME id the adacpp native path stamps
as ``face->src_id`` — so a face id is consistent across the OCC and native models (a handful
of unresolved faces get a negative synthetic id). ``seq`` is a running sequence index across
all faces of the model. Plus an ``ADA_EXT_data`` extension so the frontend enables the
design/picking path.
"""

from __future__ import annotations

import pathlib

from ada.config import logger


def convert_step_to_occ_clickable_glb(
    step_path: str | pathlib.Path,
    glb_path: str | pathlib.Path,
    linear_deflection: float = 0.0,
    angular_deflection: float = 0.5,
    store_units: str = "m",
) -> dict:
    """Convert ``step_path`` to an OCC-tessellated GLB at ``glb_path`` carrying per-face
    clickable regions. Returns a stats dict ``{solids, faces, materials}``.

    Routes all OCC access through the CadBackend's ``step_to_face_tagged_meshes`` verb — the
    single backend call that reads + per-face-meshes the STEP. This function only merges the
    result by colour and writes the GLB with the face-picking extras. The OCC backend is
    selected explicitly (this IS the OCC-tessellation track); ``linear_deflection<=0`` lets
    the backend auto-scale the chord tolerance to the model size.
    """
    import numpy as np

    from ada.cad import select_backend
    from ada.cadit.step.glb_spill import GlbSpillStore, write_glb_from_spill
    from ada.core.guid import create_guid
    from ada.extension.design_and_analysis_extension_schema import (
        AdaDesignAndAnalysisExtension,
    )
    from ada.visit.colors import Color
    from ada.visit.gltf.graph import GraphNode, GraphStore
    from ada.visit.gltf.meshes import MergedMesh, MeshType

    be = select_backend("occ")
    if not hasattr(be, "step_to_face_tagged_meshes"):
        raise RuntimeError(
            f"backend {getattr(be, 'name', be)!r} has no step_to_face_tagged_meshes "
            "(the OCC-clickable track requires the pythonocc-core backend)"
        )

    tagged = be.step_to_face_tagged_meshes(
        str(step_path),
        linear_deflection=linear_deflection,
        angular_deflection=angular_deflection,
        store_units=store_units,
    )

    root = GraphNode("root", 0, hash=create_guid())
    graph = GraphStore(root, {0: root})
    spill = GlbSpillStore()

    # colour -> material id (merge-by-colour), and the Color kept per id for the GLB writer.
    mat_of_color: dict[tuple, int] = {}
    color_by_mat: dict[int, Color] = {}
    # face_ranges_by_mat[mat_id][node_id] = [[start, len, faceId, seq], ...]
    face_ranges_by_mat: dict[int, dict[str, list]] = {}

    grey = Color(0.5, 0.5, 0.5)
    seq = 0  # running sequence index across every face of the model
    n_nodes = 0
    n_faces = 0
    try:
        for name, faces in tagged:
            if not faces:
                continue
            node = graph.add_node(
                GraphNode(name or f"node_{n_nodes}", graph.next_node_id(), hash=create_guid(), parent=root)
            )
            n_nodes += 1

            # Colour is carried per FACE, so this node's faces may span several materials.
            # Bucket them by colour; each (node, colour) becomes one contiguous draw-range —
            # one spill.add call, so its GroupReference IS the node's range in that material
            # and the per-face sub-offsets below are 0-based within it (no coalesce needed).
            by_color: dict[str, tuple[Color, list]] = {}
            for face_id, color_rgb, positions, indices in faces:
                if positions.size == 0 or indices.size == 0:
                    continue
                color = Color(*color_rgb) if color_rgb is not None else grey
                by_color.setdefault(color.hex, (color, []))[1].append((face_id, positions, indices))

            for key, (color, flist) in by_color.items():
                mat_id = mat_of_color.get(key)
                if mat_id is None:
                    mat_id = len(mat_of_color)
                    mat_of_color[key] = mat_id
                    color_by_mat[mat_id] = color

                merged_pos = np.concatenate([f[1] for f in flist]) if len(flist) > 1 else flist[0][1]
                merged_idx = np.arange(merged_pos.size // 3, dtype=np.uint32)

                sub = []
                offset = 0
                for face_id, positions, indices in flist:
                    f_len = int(indices.size)
                    sub.append([int(offset), f_len, int(face_id), int(seq)])
                    offset += f_len
                    seq += 1
                    n_faces += 1

                spill.add(mat_id, node.hash, merged_pos, merged_idx)
                face_ranges_by_mat.setdefault(mat_id, {})[node.node_id] = sub

        # Register each material's picking ranges so to_json_hierarchy emits draw_ranges_node<m>.
        empty_pos = np.empty(0, dtype=np.float32)
        empty_idx = np.empty(0, dtype=np.uint32)
        for m in spill.materials():
            if m.index_count > 0:
                graph.add_merged_mesh(
                    m.mat_id,
                    MergedMesh(empty_idx, empty_pos, None, color_by_mat.get(m.mat_id), MeshType.TRIANGLES, m.groups),
                )

        scene_metadata = dict(graph.to_json_hierarchy())  # id_hierarchy + draw_ranges_node<m>
        # Attach the per-face sub-ranges keyed identically to draw_ranges (node<m> / node_id).
        for mat_id, node_faces in face_ranges_by_mat.items():
            scene_metadata[f"face_ranges_node{mat_id}"] = node_faces

        ada_ext = AdaDesignAndAnalysisExtension()
        write_glb_from_spill(
            glb_path,
            spill,
            color_by_mat,
            ada_ext.model_dump(mode="json"),
            scene_metadata,
            base_frame=root.name,
        )
    finally:
        spill.cleanup()

    logger.info(
        "occ-clickable STEP->GLB: %s nodes, %s faces, %s material(s) -> %s",
        n_nodes,
        n_faces,
        len(color_by_mat),
        glb_path,
    )
    return {"solids": n_nodes, "faces": n_faces, "materials": len(color_by_mat)}
