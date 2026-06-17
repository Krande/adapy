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


_NO_GEOMETRY_MSG = (
    "source contains no renderable geometry — no surfaces or solids to mesh "
    "(e.g. a wireframe/points-only or empty model)"
)


def _is_empty_scene(exc: Exception) -> bool:
    # trimesh raises ValueError("Can't export empty scenes!") when the
    # tessellated scene has zero geometry (the source had no faces/solids).
    return isinstance(exc, ValueError) and "empty scene" in str(exc).lower()


def _export_assembly(asm, target: str) -> bytes:
    """Write an Assembly to a single-blob target."""
    target = target.lower()
    if target == "glb":
        buf = io.BytesIO()
        try:
            asm.to_gltf(buf)
        except ValueError as exc:
            if _is_empty_scene(exc):
                raise RuntimeError(_NO_GEOMETRY_MSG) from exc
            raise
        out = buf.getvalue()
    elif target in ("obj", "stl"):
        try:
            data = asm.to_trimesh_scene().export(file_type=target)
        except ValueError as exc:
            if _is_empty_scene(exc):
                raise RuntimeError(_NO_GEOMETRY_MSG) from exc
            raise
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


def _zip_bake_dir(out_dir: str) -> bytes:
    """Zip a baked artefact directory (must contain fea.manifest.json)."""
    import os
    import zipfile

    names = sorted(n for n in os.listdir(out_dir) if os.path.isfile(os.path.join(out_dir, n)))
    if "fea.manifest.json" not in names:
        raise RuntimeError("FEA bake produced no manifest")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for n in names:
            zf.write(os.path.join(out_dir, n), arcname=n)
    return buf.getvalue()


def _fresh_bake_dir() -> str:
    import os
    import shutil

    out_dir = "/tmp/_wasm_fea_out"
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def bake_fea(src: str, ext: str) -> bytes:
    """FEA result → streaming-viewer artefact tree, returned as a zip."""
    from ada.fem.results.artefacts import bake_fea_artefacts_from_source

    out_dir = _fresh_bake_dir()
    bake_fea_artefacts_from_source(src, out_dir)
    return _zip_bake_dir(out_dir)


def fea_result_to_glb(src: str) -> bytes:
    """Sesam SIF / SIN result deck → single tessellated GLB.

    The FEAResult.to_gltf path the worker's ``_via_fea_result`` uses — distinct
    from :func:`bake_fea`, which emits the streaming-viewer artefact *tree*.
    This is the single-GLB conversion that the converter registry exposes as
    the lone target for ``.sif``/``.sin``, so the WASM engine (and the audit
    sweep) can serve those cells instead of falling back to the worker.

    Mirrors the worker's default (step, field) pick so an auto-convert produces
    something viewable. SIN uses the cheap metadata path to choose a step
    before loading, avoiding materialising every step of a many-mode deck.
    """
    import os

    is_sin = os.path.splitext(src)[1].lower() == ".sin"
    if is_sin:
        from ada.fem.formats.sesam.results.read_sin import (
            read_sin_file,
            read_sin_metadata,
        )

        meta = read_sin_metadata(src)
        if not meta.fields or not meta.steps:
            raise RuntimeError("SIN result has no RV* result fields")
        result = read_sin_file(src, step=int(meta.steps[0]))
    else:
        from ada.fem.formats.sesam.results.read_sif import read_sif_file

        result = read_sif_file(src)

    return _fea_result_glb_bytes(result)


def _fea_result_glb_bytes(result) -> bytes:
    """Tessellate a :class:`FEAResult`'s first (step, field) to a single GLB.
    Shared by the path-based and streamed SIN→GLB entrypoints."""
    steps = result.get_steps()
    fields = result.get_results_grouped_by_field_value()
    if not steps:
        raise RuntimeError("FEA result contains no steps to render")
    if not fields:
        raise RuntimeError("FEA result contains no nodal/element fields to render")

    out_path = "/tmp/_wasm_fea_result.glb"
    result.to_gltf(out_path, step=int(steps[0]), field=next(iter(fields.keys())))
    with open(out_path, "rb") as fh:
        out = fh.read()
    if not out:
        raise RuntimeError("FEA result GLB export produced no bytes")
    return out


def _streamed_sin_result(fetcher, step: int | None = None, *, pick_first_step: bool = False):
    """Read a Sesam SIN into a :class:`FEAResult` over a *range fetcher*
    instead of a local file — the streaming path for the browser, where a
    multi-GB SIN can't be staged in wasm memory.

    ``fetcher`` is any object exposing ``size() -> int`` and
    ``fetch(offset, length) -> bytes`` (an HTTP-``Range`` / ``fetch`` reader
    bridged in by the host). It feeds a ``PagedByteSource`` so only touched
    pages are pulled — the 5.4 GB file never sits in wasm memory; only the
    materialised result does.

    ``step``: read just that IRES. ``pick_first_step``: when no explicit
    step, discover and read only the first RV* step (the single-GLB path).
    Otherwise (the bake) read **all** steps. One ``SinFile`` is reused for
    discovery and the read so the page cache stays warm.
    """
    import pathlib

    import numpy as np

    from ada.fem.formats.sesam.results.byte_source import PagedByteSource
    from ada.fem.formats.sesam.results.read_sif import Sif2Mesh
    from ada.fem.formats.sesam.results.read_sin import _RV_TYPE_NAMES, SinReader
    from ada.fem.formats.sesam.results.sin_reader import SinFile

    sin = SinFile(source=PagedByteSource(fetcher))
    try:
        if step is None and pick_first_step:
            steps: set[int] = set()
            for rv in _RV_TYPE_NAMES:
                if rv in sin.type_blocks:
                    ires = sin.gather_first_words(rv)
                    if ires.size:
                        steps.update(int(x) for x in np.unique(ires.astype(np.int64)).tolist())
            if not steps:
                raise RuntimeError("SIN result has no RV* result steps")
            step = sorted(steps)[0]
        reader = SinReader(sin=sin, step=None if step is None else int(step))
        reader.load()
        return Sif2Mesh(reader).convert(pathlib.Path("remote.sin"))
    finally:
        sin.close()


def _streamed_sin_bake(fetcher) -> bytes:
    """Bake a SIN → streaming-viewer artefact tree (zip), reading the source
    over a range fetcher and **one step at a time**.

    Two compounding bounds keep this inside the wasm32 ceiling for an
    arbitrarily large deck: the source is range-streamed (the file never
    lands in wasm memory), and :class:`SinStreamReader` materialises only
    ~2 steps' worth of ``FEAResult`` at a time instead of the whole
    multi-step result. The artefact-tree blobs are still written to MEMFS
    before zipping (per-step *upload* streaming is a further step)."""
    from ada.fem.formats.sesam.results.byte_source import PagedByteSource
    from ada.fem.formats.sesam.results.read_sin import SinStreamReader
    from ada.fem.results.artefacts import bake_artefacts

    out_dir = _fresh_bake_dir()
    with SinStreamReader(PagedByteSource(fetcher)) as reader:
        bake_artefacts(reader, out_dir, src="remote")
    return _zip_bake_dir(out_dir)


def run_stream(fmt: str, ext: str, target: str, fetcher, step: int | None = None) -> bytes:
    """Streamed counterpart of :func:`run` — convert from a *range fetcher*
    rather than a fully-staged source file.

    Used by the browser worker for sources too large to hold in wasm memory:
    the host bridges an HTTP-``Range`` / ``fetch`` reader as ``fetcher``
    (``size()`` / ``fetch(offset, length) -> bytes``) and this streams over
    it. Supports ``fea_glb`` (Sesam ``.sin`` → single-step GLB) and ``fea``
    (``.sin`` → artefact-tree bake zip); other formats stay on the buffered
    :func:`run` path.
    """
    fmt = (fmt or "").lower()
    target = (target or "glb").lower()
    if fmt == "fea":
        return _streamed_sin_bake(fetcher)
    if fmt == "fea_glb":
        return _fea_result_glb_bytes(_streamed_sin_result(fetcher, step=step, pick_first_step=True))
    raise RuntimeError(f"wasm_convert: streaming source not supported for fmt={fmt!r} (only fea / fea_glb)")


def run(fmt: str, ext: str, target: str, src: str) -> bytes:
    """Convert ``src`` (a path on the pyodide FS) and return the output bytes.

    ``fmt`` is the wasm source-format class (sat/ifc/step/fem/genie/mesh/fea/
    fea_glb); ``ext`` the source file extension; ``target`` the requested
    output format.
    """
    fmt = (fmt or "").lower()
    target = (target or "glb").lower()

    if fmt == "fea":
        return bake_fea(src, ext)
    if fmt == "fea_glb":
        return fea_result_to_glb(src)
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
