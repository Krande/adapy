"""Single-source conversion dispatch for the WASM / pyodide engine.

The browser Web Worker (``src/frontend/.../pyodide_worker.js``) and the node
sweep driver (``tools/pyodide-test/wasm_sweep_driver.js``) used to each carry
their own copy of this logic in embedded Python strings — and drifted (the same
bugs had to be fixed in both). The conversion logic is adapy's, not the host's,
so it lives here, shipped in the pyodide wheel. Each host now only does the
genuinely host-specific work — boot pyodide, install the wheels/packages, move
bytes — then calls :func:`run`.

The host is responsible for having installed the right stack before calling
(numpy/Pillow always; trimesh+pyquaternion for any ada import; adacpp+adapy for
CAD; ifcopenshell for ifc source/target; h5py for FEA). ``run`` assumes its
dependencies are importable and raises a normal Python exception otherwise,
which the host surfaces as a failed conversion.

Everything is lazily imported so importing this module is cheap and safe.
"""

from __future__ import annotations

import io

# Mesh containers trimesh round-trips (glb is a valid source too: glb→obj/stl).
_MESH_EXTS = {"obj", "stl", "ply", "gltf", "dae", "off", "glb"}
_FEA_EXTS = {"rmed", "med", "sif", "sin"}
_DECK_TARGETS = {"inp", "fem", "med"}


def _load_assembly(fmt: str, src: str):
    """Materialise an ada Assembly from a source file via the right factory."""
    import ada

    if fmt == "sat":
        return ada.from_acis(src)
    if fmt == "ifc":
        return ada.from_ifc(src)
    if fmt in ("step", "stp"):
        # The kernel-free stream reader — the OCC reader is not wasm-safe.
        return ada.from_step(src, reader="stream")
    if fmt == "fem":
        return ada.from_fem(src, create_concept_objects=True)
    if fmt == "genie":
        return ada.from_genie_xml(src)
    raise RuntimeError(f"wasm_convert: no loader for source format {fmt!r}")


def _export_assembly(asm, target: str) -> bytes:
    """Write an Assembly to a single-blob target."""
    target = target.lower()
    if target == "glb":
        buf = io.BytesIO()
        asm.to_gltf(buf)
        out = buf.getvalue()
    elif target in ("obj", "stl"):
        data = asm.to_trimesh_scene().export(file_type=target)
        out = data.encode() if isinstance(data, str) else bytes(data)
    elif target in ("step", "stp"):
        asm.to_stp("/tmp/_wasm_out.step")
        with open("/tmp/_wasm_out.step", "rb") as fh:
            out = fh.read()
    elif target == "xml":
        asm.to_genie_xml("/tmp/_wasm_out.xml")
        with open("/tmp/_wasm_out.xml", "rb") as fh:
            out = fh.read()
    elif target == "ifc":
        asm.to_ifc("/tmp/_wasm_out.ifc")
        with open("/tmp/_wasm_out.ifc", "rb") as fh:
            out = fh.read()
    else:
        raise RuntimeError(f"wasm_convert: unsupported target {target!r}")
    if not out:
        raise RuntimeError(f"wasm_convert: produced an empty {target}")
    return out


def _step_to_glb(data: bytes) -> bytes:
    """STEP→GLB fast path: straight through the adacpp backend (no ada loader)."""
    import ada.cad

    backend = ada.cad.select_backend(prefer="adacpp")
    shape = backend.read_step_bytes(data)
    return backend.write_glb_bytes(shape)


def _ifc_to_glb(src: str) -> bytes:
    """IFC→GLB fast path: ifcopenshell geometry iterator → trimesh GLB."""
    import ifcopenshell
    import ifcopenshell.geom
    import numpy as np
    import trimesh

    ifc = ifcopenshell.open(src)
    settings = ifcopenshell.geom.settings()
    try:
        settings.set("use-world-coords", True)
    except Exception:
        pass
    iterator = ifcopenshell.geom.iterator(settings, ifc)
    scene = trimesh.Scene()
    n = 0
    if iterator.initialize():
        while True:
            try:
                shape = iterator.get()
                geom = shape.geometry
                verts = np.asarray(geom.verts, dtype=np.float32).reshape(-1, 3)
                faces = np.asarray(geom.faces, dtype=np.int32).reshape(-1, 3)
                if faces.size:
                    scene.add_geometry(
                        trimesh.Trimesh(vertices=verts, faces=faces, process=False),
                        node_name=(getattr(shape, "name", None) or shape.guid or f"m{n}"),
                    )
                    n += 1
            except Exception:
                pass
            if not iterator.next():
                break
    if n == 0:
        raise RuntimeError("no meshable geometry produced from this IFC")
    buf = io.BytesIO()
    scene.export(buf, file_type="glb")
    return buf.getvalue()


def _to_glb(fmt: str, src: str) -> bytes:
    """Produce GLB for a geometry source via its most reliable path."""
    if fmt == "ifc":
        return _ifc_to_glb(src)
    if fmt in ("step", "stp"):
        with open(src, "rb") as fh:
            return _step_to_glb(fh.read())
    return _export_assembly(_load_assembly(fmt, src), "glb")


def _glb_to_mesh(glb: bytes, target: str) -> bytes:
    """Convert in-memory GLB to obj/stl via trimesh.

    OBJ/STL are produced through GLB rather than asm.to_trimesh_scene() because
    the latter trips trimesh's OBJ exporter on Path3D (beam wireframe) geometry
    and yields an empty scene for sources whose shapes only tessellate on the
    GLB path (e.g. the STEP stream reader). Going via GLB makes obj/stl as
    reliable as glb itself.
    """
    import trimesh

    scene = trimesh.load(io.BytesIO(glb), file_type="glb")
    if not isinstance(scene, trimesh.Scene):
        scene = trimesh.Scene(scene)
    # GLB can carry line primitives (FEM mesh edges, beam wireframes) which
    # trimesh loads as Path3D — the OBJ/STL surface exporters choke on those
    # ('Path3D' has no .visual). Drop every non-surface geometry first.
    for key in [k for k, g in scene.geometry.items() if not isinstance(g, trimesh.Trimesh)]:
        scene.delete_geometry(key)
    if len(scene.geometry) == 0:
        raise RuntimeError("no surface geometry to export (source has only line/point geometry)")
    data = scene.export(file_type=target)
    out = data.encode() if isinstance(data, str) else bytes(data)
    if not out:
        raise RuntimeError(f"produced an empty {target}")
    return out


def _convert_mesh(src: str, ext: str, target: str) -> bytes:
    import trimesh

    ext = ext.lower()
    target = (target or "glb").lower()
    loaded = trimesh.load(src, file_type=ext, process=False)
    scene = loaded if isinstance(loaded, trimesh.Scene) else trimesh.Scene(loaded)
    if len(scene.geometry) == 0:
        raise RuntimeError("no meshable geometry produced from this file")
    if target == "glb":
        buf = io.BytesIO()
        scene.export(buf, file_type="glb")
        out = buf.getvalue()
    else:
        data = scene.export(file_type=target)
        out = data.encode() if isinstance(data, str) else bytes(data)
    if not out:
        raise RuntimeError(f"mesh export produced an empty {target}")
    return out


def bake_fea(src: str, ext: str) -> bytes:
    """FEA result → streaming-viewer artefact tree, returned as a zip."""
    import os
    import shutil
    import zipfile

    from ada.fem.results.artefacts import bake_fea_artefacts_from_source

    out_dir = "/tmp/_wasm_fea_out"
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    bake_fea_artefacts_from_source(src, out_dir)
    names = sorted(n for n in os.listdir(out_dir) if os.path.isfile(os.path.join(out_dir, n)))
    if "fea.manifest.json" not in names:
        raise RuntimeError("FEA bake produced no manifest")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for n in names:
            zf.write(os.path.join(out_dir, n), arcname=n)
    return buf.getvalue()


def run(fmt: str, ext: str, target: str, src: str) -> bytes:
    """Convert ``src`` (a path on the pyodide FS) and return the output bytes.

    ``fmt`` is the wasm source-format class (sat/ifc/step/fem/genie/mesh/fea);
    ``ext`` the source file extension; ``target`` the requested output format.
    """
    fmt = (fmt or "").lower()
    target = (target or "glb").lower()

    if fmt == "fea":
        return bake_fea(src, ext)
    if fmt == "mesh":
        return _convert_mesh(src, ext, target)
    if fmt == "fem" and target in _DECK_TARGETS:
        from ada.fem.formats.deck_convert import fem_deck_to_bytes

        return fem_deck_to_bytes(src, target)
    if target == "glb":
        return _to_glb(fmt, src)
    if target in ("obj", "stl"):
        # obj/stl via the reliable GLB tessellation path (see _glb_to_mesh).
        return _glb_to_mesh(_to_glb(fmt, src), target)
    # Structure-preserving writers (ifc / xml / step) keep the B-rep.
    return _export_assembly(_load_assembly(fmt, src), target)
