"""An equipment type's geometry read from its linked CAD asset: the bounding box an
``equipment_bbox`` job infers (with its preview GLB), and the mesh a procedural build splices in.

Pure: bytes in, numbers and bytes out, no storage, queue or database -- so the worker's handler
(``formats/equipment.py``), a queue-less server and the browser (``ada.comms.rest.wasm_jobs``) run
the same code.
"""

from __future__ import annotations


def infer_equipment_geometry(data: bytes, ext: str, z_up: bool = True) -> tuple[dict, bytes]:
    """Read a CAD/mesh asset, returning its axis-aligned bounding-box extents
    ``{lx, ly, lz}`` (in metres) and a preview GLB for the sidecar viewer. Mesh
    formats load via trimesh; CAD formats via the matching ada reader.

    ``z_up=True`` (default) takes the asset as authored in adapy's **Z-up**
    convention (ada readers and ada-exported GLBs are Z-up): ``lz`` = the Z extent
    = height and the mesh is NOT re-oriented — measuring/previewing it verbatim
    keeps lz == the CAD's real vertical extent. ``z_up=False`` treats a mesh asset
    (.glb/.gltf/.stl/.obj) as glTF-spec **Y-up** and re-orients it Y-up→Z-up
    (rotate +90° about X) before measuring and previewing, so the inferred bbox
    and the preview GLB are both in adapy's Z-up frame. ``z_up`` is ignored for
    ada-reader formats (already Z-up)."""
    import pathlib as _pl
    import tempfile as _tf

    ext = ext.lower()
    with _tf.TemporaryDirectory(prefix="eqbbox_") as tmp:
        src = _pl.Path(tmp) / f"source{ext}"
        src.write_bytes(data)
        if ext in (".glb", ".gltf", ".stl", ".obj"):
            import numpy as np
            import trimesh

            scene = trimesh.load(src, force="scene")
            if not z_up:
                # Re-orient a Y-up authored mesh into adapy's Z-up frame; both the
                # measured bounds and the exported preview then match Z-up.
                scene.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
            bounds = scene.bounds
            if z_up and ext in (".glb", ".gltf"):
                preview = data
            else:
                preview = scene.export(file_type="glb")
        else:
            import ada

            readers = {
                ".step": ada.from_step,
                ".stp": ada.from_step,
                ".ifc": ada.from_ifc,
                ".sat": ada.from_acis,
                ".xml": ada.from_genie_xml,
            }
            reader = readers.get(ext)
            if reader is None:
                raise ValueError(f"unsupported CAD extension {ext!r} for bbox inference")
            a = reader(src)
            bounds = a.to_trimesh_scene().bounds
            out = _pl.Path(tmp) / "preview.glb"
            a.to_gltf(out)
            preview = out.read_bytes()

    if bounds is None:
        raise ValueError("could not determine geometry bounds (empty model?)")
    lo, hi = bounds[0], bounds[1]
    bbox = {"lx": float(hi[0] - lo[0]), "ly": float(hi[1] - lo[1]), "lz": float(hi[2] - lo[2])}
    return bbox, preview


def load_cad_mesh(data: bytes, ext: str, z_up: bool = True):
    """Load a CAD/mesh asset into a single concatenated trimesh (graph
    transforms baked). Used to splice real equipment geometry into a compiled
    procedural model.

    ``z_up=True`` (default) takes the asset verbatim (adapy Z-up convention).
    ``z_up=False`` re-orients a mesh asset (.glb/.gltf/.stl/.obj) from glTF-spec
    Y-up into Z-up (rotate +90° about X) before baking, so the spliced geometry
    lands in the same frame as the inferred bbox. Ignored for ada-reader formats
    (already Z-up)."""
    import pathlib as _pl
    import tempfile as _tf

    import numpy as np
    import trimesh

    ext = ext.lower()
    with _tf.TemporaryDirectory(prefix="eqcad_") as tmp:
        src = _pl.Path(tmp) / f"source{ext}"
        src.write_bytes(data)
        if ext in (".glb", ".gltf", ".stl", ".obj"):
            scene = trimesh.load(src, force="scene")
            if not z_up:
                scene.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
        else:
            import ada

            readers = {
                ".step": ada.from_step,
                ".stp": ada.from_step,
                ".ifc": ada.from_ifc,
                ".sat": ada.from_acis,
                ".xml": ada.from_genie_xml,
            }
            reader = readers.get(ext)
            if reader is None:
                raise ValueError(f"unsupported CAD extension {ext!r} for geometry splice")
            scene = reader(src).to_trimesh_scene()
    return scene.dump(concatenate=True)
