"""merge-cad utility: recover analytic CAD from a FEM shell mesh.

The FEM→STEP/IFC writers are dominated by plate *count*; the coplanar merge is
meant to collapse edge-adjacent coplanar shells into big union plates but often
barely reduces the count. This utility runs the surface-recognition + merge on the
loaded FEM source and, depending on ``action``, either just previews the partition
(colour each facet by its merge group, no geometry built) or actually builds the
merged CAD — analytic faces (``generate``) or hollow tube solids with boolean
joints (``solids``) — and renders a viewable, per-object-pickable GLB overlay.

Registered as the ``merge-cad`` worker utility. It swaps between merge
algorithms and forwards their parameters (see :data:`ada.fem.formats.merge_preview.ALGORITHMS`),
so a new curved-region strategy is exposed by adding it there — no change here.

Output (viewer-ops): one ``add_overlay_geometry`` op pointing at the colorized
preview GLB the utility writes + uploads (auto-disposed ``_overlays/`` blob), a
``legend``, and a ``summary`` carrying the partition stats (raw primitives,
achieved plates, actual-vs-ideal reduction, fell-back plates, % curved).
"""

from __future__ import annotations

import pathlib
import tempfile

from ada.comms.rest.utility import utility

# Auto-disposed overlay namespace (same lifecycle bucket the diff utility uses),
# kept out of the persistent admin-managed _derived/ tree.
_OVERLAY_PREFIX = "_overlays/"

_FEM_EXTS = {".fem", ".inp", ".sif", ".sin", ".bdf", ".nas", ".med", ".rmed"}


def _put_overlay_glb(storage, key: str, glb_path: str) -> None:
    """meshopt-compress the overlay GLB (EXT_meshopt_compression — the vertex/index entropy
    codec the viewer decodes) and upload it gzip-at-rest, so a dense generated/preview overlay
    downloads small on mobile instead of the raw multi-MB trimesh export."""
    from ada.visit.gltf.meshopt import meshopt_compress_glb

    packed = str(meshopt_compress_glb(glb_path, str(pathlib.Path(glb_path).with_suffix(".mo.glb"))))
    try:
        with open(packed, "rb") as fh:
            storage.put_bytes(key, fh.read(), content_encoding="gzip")
    finally:
        if packed != str(glb_path):
            try:
                pathlib.Path(packed).unlink()
            except OSError:
                pass


@utility(
    name="merge-cad",
    description=(
        "Recover analytic CAD from a FEM shell mesh: preview the planned plate/tube merge, or build "
        "it — merged plates + curved panels (generate), or hollow tube members with boolean joints "
        "(solids). Renders a viewable, per-object-pickable GLB overlay."
    ),
    kwargs=[
        {
            "name": "action",
            "type": "enum",
            "default": "preview",
            "enum": ["preview", "generate", "solids"],
            "description": "preview = colorized partition overlay (no geometry built); generate = build the "
            "merged CAD (flat + curved faces) and render a GLB; solids = build analytic SOLIDS (tubes as "
            "hollow members with wall thickness, joints resolved with boolean CSG) and render a GLB.",
        },
        {
            "name": "algorithm",
            "type": "enum",
            "default": "auto",
            "enum": ["auto", "none", "coplanar", "planar", "surface", "classify", "panel"],
            "description": "Merge strategy: auto (the PRODUCTION emit — cylinder ident + curved B-spline panels "
            "+ planar merge, matches STEP/IFC), none (raw baseline), coplanar, planar (flat region growing), "
            "surface (curved→B-spline), classify (recognise planar/cylinder/freeform), panel (WIP).",
        },
        {
            "name": "mode",
            "type": "enum",
            "default": "class",
            "enum": ["class", "status", "achieved", "component"],
            "description": "Preview colouring: class (per output primitive — cylinder/curved/planar/facet, pair "
            "with algorithm=auto), status (green=merged/red=fell-back), achieved (per collapsed plate), "
            "component (per intended group).",
        },
        {
            "name": "ndigits",
            "type": "int",
            "default": 6,
            "description": "Coplanarity rounding tolerance (decimal places on normal + plane offset).",
        },
        {
            "name": "angle_tol",
            "type": "float",
            "default": 30.0,
            "description": "Max fold angle (deg) for curved-region growing (surface/panel strategies).",
        },
        {
            "name": "min_patch_quads",
            "type": "int",
            "default": 12,
            "description": "Smallest curved patch (in quads) worth fitting a surface to (surface/panel).",
        },
        {
            "name": "max_dev",
            "type": "float",
            "default": 0.0,
            "description": "Planar strategy: max distance (model units) a facet may sit off the patch "
            "plane. 0 = auto (1e-3 of the mesh bbox diagonal).",
        },
    ],
    affects=("scene.overlay",),
)
def merge_preview(
    src_path,
    *,
    storage,
    scope,
    on_progress,
    action="preview",
    algorithm="auto",
    mode="class",
    ndigits=6,
    angle_tol=30.0,
    min_patch_quads=12,
    max_dev=0.0,
    source_key=None,
    **_,
):
    import ada
    from ada.fem.formats.merge_preview import analyze_part, write_preview_glb

    src_path = pathlib.Path(src_path)
    if src_path.suffix.lower() not in _FEM_EXTS:
        raise ValueError(
            f"merge-preview needs a FEM source (shell mesh); got {src_path.suffix!r}. "
            f"Supported: {sorted(_FEM_EXTS)}"
        )

    # Name persisted overlays by the MODEL, not the worker's random temp file: the utils menu
    # filters saved overlays by loaded-model stem, so an overlay must carry the real model name.
    model = pathlib.PurePosixPath(source_key).stem if source_key else src_path.stem

    on_progress("loading-fem", 0.35)
    asm = ada.from_fem(src_path)

    if action in ("generate", "solids"):
        return _generate(asm, model, algorithm, storage, on_progress, solids=(action == "solids"))

    on_progress(f"partitioning ({algorithm})", 0.55)
    res = analyze_part(
        asm,
        strategy=algorithm,
        ndigits=int(ndigits),
        angle_tol=float(angle_tol),
        min_patch_quads=int(min_patch_quads),
        max_dev=(float(max_dev) or None),  # 0 → auto
    )

    on_progress("rendering-preview", 0.8)
    glb_tmp = tempfile.mkstemp(suffix=".glb")[1]
    try:
        write_preview_glb(res, glb_tmp, mode=mode)
        overlay_key = f"{_OVERLAY_PREFIX}{model}.merge-{algorithm}-{mode}.glb"
        _put_overlay_glb(storage, overlay_key, glb_tmp)
    finally:
        try:
            pathlib.Path(glb_tmp).unlink()
        except OSError:
            pass

    s = res.stats
    if mode == "status":
        legend = [
            {"label": "merged (→ 1 plate)", "color": "#46b45a"},
            {"label": "fell back (unmerged)", "color": "#d23c3c"},
        ]
    elif mode == "class":
        legend = [
            {"label": "cylinder", "color": "#46be5a"},
            {"label": "curved (B-spline)", "color": "#9659dc"},
            {"label": "planar (merged)", "color": "#4678dc"},
            {"label": "facet (residual)", "color": "#d23c3c"},
        ]
    else:
        legend = [{"label": "distinct merge group", "color": "#7f7fff"}]
    on_progress("done", 1.0)
    return {
        "ops": [
            {
                "op": "add_overlay_geometry",
                "blob_key": overlay_key,
                "label": f"merge-{algorithm} ({mode})",
                "color": "#46b45a",
            }
        ],
        "legend": legend,
        "summary": s,
    }


_GEN_CLASS_COLOR = {
    "cylinder": [70, 190, 90, 255],  # green
    "curved": [150, 90, 220, 255],  # purple
    "planar": [70, 120, 220, 255],  # blue
    "tube": [220, 150, 60, 255],  # orange (solids mode: hollow tube member)
}


def _drop_sliver_tris(pos, idx):
    """Drop Manifold's degenerate sliver triangles (near-zero shortest altitude) left along boolean
    cut boundaries — the "slivers at the tube ends". Keeps the mesh welded and adds NO normals: the
    geometry is clean, so the viewer's own smoothing renders the cylinder walls smooth (carried
    hard-edge normals faceted them into visible axial edges). A triangle is dropped when its shortest
    altitude falls below 3% of the median — the real wall/cap/cut triangles cluster ~50× above that,
    and the residue is coincident with real triangles anyway so nothing structural is lost.

    ``pos`` (N,3) float, ``idx`` (M*3,) int for ONE member's mesh. Returns (pos, idx) unchanged
    except for the removed triangles (``pos`` untouched; unused vertices are harmless)."""
    import numpy as np

    tris = np.asarray(idx, dtype=np.int64).reshape(-1, 3)
    p = np.asarray(pos, dtype=np.float64)
    two_area = np.linalg.norm(np.cross(p[tris[:, 1]] - p[tris[:, 0]], p[tris[:, 2]] - p[tris[:, 0]]), axis=1)
    e = np.stack(
        [
            np.linalg.norm(p[tris[:, 1]] - p[tris[:, 0]], axis=1),
            np.linalg.norm(p[tris[:, 2]] - p[tris[:, 1]], axis=1),
            np.linalg.norm(p[tris[:, 0]] - p[tris[:, 2]], axis=1),
        ],
        axis=1,
    )
    min_alt = two_area / np.maximum(e.max(axis=1), 1e-12)  # shortest triangle altitude
    nz = min_alt[two_area > 1e-12]
    thresh = 0.03 * float(np.median(nz)) if len(nz) else 0.0
    keep = (two_area > 1e-12) & (min_alt > thresh)
    return pos, tris[keep].reshape(-1).astype(np.uint32)


def _generate(asm, model, algorithm, storage, on_progress, solids=False):
    """Render the merged CAD to a viewable GLB overlay that behaves like a first-class model
    (per-plate picking + object tree).

    ``solids=False`` (generate): the SAME analytic faces the STEP/IFC 'auto' emit produces —
    cylinders + curved B-spline panels + merged flat plates. ``solids=True`` (solids): analytic
    SOLIDS — each tube is a hollow member with its real wall thickness and joints are resolved with
    boolean CSG (:func:`iter_fem_analytic_solids`); non-tube geometry stays as faces.

    Each item is streamed as its own NGEOM root, so libtess2 (adacpp, OCC-free, thread-parallel)
    returns one BatchMesh group PER item. Items are decimated (meshopt, fast/light), grouped by
    class into per-colour MergedMeshes with a GroupReference per item, and written through the same
    GLB writer models use — so the GLB carries id_hierarchy + draw_ranges_node* (per-item picking)
    and the ADA_EXT_data extension (object tree)."""
    import os as _os

    import numpy as np
    import trimesh

    from ada.cad import active_backend
    from ada.core.guid import create_guid
    from ada.extension.design_and_analysis_extension_schema import AdaDesignAndAnalysisExtension
    from ada.extension.design_extension_schema import DesignDataExtension
    from ada.geom.booleans import BooleanResult
    from ada.geom.solids import ExtrudedAreaSolid
    from ada.geom.surfaces import (
        BSplineSurfaceWithKnots,
        CylindricalSurface,
        OpenShell,
        ShellBasedSurfaceModel,
    )
    from ada.visit.colors import Color
    from ada.visit.gltf.graph import GraphNode, GraphStore
    from ada.visit.gltf.meshes import GroupReference, MergedMesh, MeshType
    from ada.visit.gltf.store import merged_mesh_to_trimesh_scene

    def _cls(g):
        # tubes are a hollow ExtrudedAreaSolid (uncut) or a BooleanResult (wall - saddle cuts);
        # faces classify by their surface
        if isinstance(g, (BooleanResult, ExtrudedAreaSolid)):
            return "tube"
        s = getattr(g, "face_surface", None)
        if isinstance(s, CylindricalSurface):
            return "cylinder"
        if isinstance(s, BSplineSurfaceWithKnots):
            return "curved"
        return "planar"

    on_progress("recognising surfaces", 0.4)
    # reconstruct_curved=False: the rectangular grid B-spline recovery over-merges bent regions into
    # folded grids (gaps at e.g. a slanted roof); the flat plane bucket recovers those with true
    # bounding edges instead. Cylinders/tubes are still detected. See iter_fem_analytic_faces.
    if solids:
        from ada.fem.formats.mesh_faces import iter_fem_analytic_solids

        # already (id, geometry) with geometry a serializable root: a BooleanResult tube solid or a face
        geoms = [g for _id, g in iter_fem_analytic_solids(asm, reconstruct_curved=False)]
        cls_of = [_cls(g) for g in geoms]
    else:
        from ada.fem.formats.mesh_faces import iter_fem_analytic_faces

        faces = list(iter_fem_analytic_faces(asm, reconstruct_curved=False))
        cls_of = [_cls(f) for f in faces]  # classify the FACE (carries face_surface) before wrapping
        geoms = [ShellBasedSurfaceModel(sbsm_boundary=[OpenShell(cfs_faces=[f])]) for f in faces]

    # one NGEOM root PER item → one BatchMesh group per item (pickable unit)
    items = [(f"{cls_of[i]}_{i}", g) for i, g in enumerate(geoms)]

    on_progress("tessellating (libtess2)", 0.6)
    be = active_backend()
    if hasattr(be, "tessellate_stream"):
        # deflection=0 (auto): adacpp's refine_uv derives a scale-relative chord tolerance per face
        # from the surface's own approx_size (~0.5% of the patch diagonal), so a coarse cubic panel
        # stays a few k tris. Whole-model call (not the per-solid STEP->GLB pool) → use all cores;
        # plates parallelise.
        bm = be.tessellate_stream(items, pipeline="libtess2", deflection=0.0, threads=(_os.cpu_count() or 1))
    else:
        # Backend without the adacpp NGEOM streaming tessellator (e.g. OccBackend): build + tessellate
        # each plate through the CadBackend object path and combine into the same BatchMesh. node_id=i
        # (item position) matches tessellate_stream, so the per-plate assembly below is identical.
        from ada.cad import tessellate_batch_via_loop
        from ada.geom import Geometry

        shapes = [be.build(Geometry(id=iid, geometry=geom, color=None, transforms=None)) for iid, geom in items]
        bm = tessellate_batch_via_loop(be, shapes)

    on_progress("building pickable model", 0.8)
    all_pos = np.asarray(bm.positions, dtype=np.float32).reshape(-1, 3)
    all_idx = np.asarray(bm.indices, dtype=np.uint32)

    # Assemble per-class (per-colour) MergedMeshes, each with one GroupReference per plate, plus a
    # root→class→plate node tree. bm.groups are in item order, so group i is face i.
    root = GraphNode(model, "0")
    nodes: dict = {"0": root}
    nid = 1
    class_nodes: dict = {}
    acc: dict = {}  # cls -> {pos:[], idx:[], nrm:[], groups:[], vbase, ibase}
    for i, g in enumerate(bm.groups):
        c = cls_of[i]
        gp = all_pos[g.vstart : g.vstart + g.vlength]
        gi = all_idx[g.start : g.start + g.length] - g.vstart
        gn = None
        if solids and len(gi) >= 3:
            # drop Manifold's degenerate cut slivers; no normals (viewer smooths — carried hard-edge
            # normals faceted the cylinder walls into visible axial edges).
            gp, gi = _drop_sliver_tris(gp, gi)
        if len(gi) == 0:
            continue
        if c not in acc:
            acc[c] = {"pos": [], "idx": [], "nrm": [], "groups": [], "vbase": 0, "ibase": 0}
            cn = GraphNode(c, str(nid), parent=root)
            root.children.append(cn)
            nodes[str(nid)] = cn
            class_nodes[c] = cn
            nid += 1
        a = acc[c]
        pnode = GraphNode(g.node_id, str(nid), parent=class_nodes[c])
        class_nodes[c].children.append(pnode)
        nodes[str(nid)] = pnode
        nid += 1
        a["groups"].append(GroupReference(node_ref=pnode, start=a["ibase"], length=int(gi.size)))
        a["pos"].append(gp)
        a["idx"].append(gi + a["vbase"])
        if gn is not None:
            a["nrm"].append(gn)
        a["vbase"] += len(gp)
        a["ibase"] += int(gi.size)

    graph = GraphStore(top_level=root, nodes=nodes)
    scene = trimesh.Scene(base_frame=root.name)  # merged_mesh_to_trimesh_scene parents to top_level.name
    total_verts = 0
    for buffer_id, (c, a) in enumerate(acc.items()):
        pos = np.concatenate(a["pos"]).reshape(-1).astype(np.float32)
        idx = np.concatenate(a["idx"]).astype(np.uint32)
        nrm = np.concatenate(a["nrm"]).reshape(-1).astype(np.float32) if a["nrm"] else None
        total_verts += len(pos) // 3
        rgb = _GEN_CLASS_COLOR.get(c, [180, 180, 180, 255])
        color = Color(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255)
        mm = MergedMesh(
            indices=idx, position=pos, normal=nrm, material=color, type=MeshType.TRIANGLES, groups=a["groups"]
        )
        merged_mesh_to_trimesh_scene(scene, mm, color, buffer_id, graph)

    # id_hierarchy + draw_ranges_node* → scene.extras (per-plate picking + tree)
    scene.metadata.update(graph.to_json_hierarchy())
    # Minimal-but-valid ADA_EXT_data so the frontend enables the design (picking) path.
    ada_ext = AdaDesignAndAnalysisExtension(
        design_objects=[DesignDataExtension(name=model)],
        simulation_objects=[],
        assembly_guid=create_guid(),
    ).model_dump(mode="json")

    def _tree_pp(tree):
        tree.setdefault("extensions", {})["ADA_EXT_data"] = ada_ext
        used = tree.setdefault("extensionsUsed", [])
        if "ADA_EXT_data" not in used:
            used.append("ADA_EXT_data")
        for mat in tree.get("materials", []):
            mat["doubleSided"] = True

    on_progress("writing-glb", 0.9)
    glb_tmp = tempfile.mkstemp(suffix=".glb")[1]
    try:
        data = scene.export(file_type="glb", tree_postprocessor=_tree_pp)
        pathlib.Path(glb_tmp).write_bytes(data)
        kind = "solids" if solids else "generated"
        overlay_key = f"{_OVERLAY_PREFIX}{model}.merge-{algorithm}-{kind}.glb"
        _put_overlay_glb(storage, overlay_key, glb_tmp)
    finally:
        try:
            pathlib.Path(glb_tmp).unlink()
        except OSError:
            pass

    on_progress("done", 1.0)
    counts: dict = {}
    for c in cls_of:
        counts[c] = counts.get(c, 0) + 1
    return {
        "ops": [
            {
                "op": "add_overlay_geometry",
                "blob_key": overlay_key,
                "label": f"{kind} ({algorithm})",
                "color": "#46b45a",
            }
        ],
        "legend": (
            [
                {"label": "tube (solid member)", "color": "#dc963c"},
                {"label": "curved (B-spline)", "color": "#9659dc"},
                {"label": "planar (merged plate)", "color": "#4678dc"},
            ]
            if solids
            else [
                {"label": "cylinder", "color": "#46be5a"},
                {"label": "curved (B-spline)", "color": "#9659dc"},
                {"label": "planar (merged plate)", "color": "#4678dc"},
            ]
        ),
        "summary": {
            "action": "solids" if solids else "generate",
            "algorithm": algorithm,
            "faces": len(cls_of),
            "tube_solids": counts.get("tube", 0),
            "cylinder_faces": counts.get("cylinder", 0),
            "curved_faces": counts.get("curved", 0),
            "planar_faces": counts.get("planar", 0),
            "plates": len(cls_of),
            "vertices": int(total_verts),
        },
    }
