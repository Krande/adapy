"""Single-source conversion dispatch for the WASM / pyodide engine.

The browser Web Worker (``src/frontend/.../pyodide_worker.js``) and the node
sweep driver (``scripts/testing/pyodide-test/wasm_sweep_driver.js``) used to each carry
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

Progress: :func:`run` and :func:`run_stream` take ``on_progress(stage, fraction)``, which
the browser worker posts to the page as it goes (reading → building the scene →
tessellating N objects → writing the output → done). See :mod:`ada.cadit.wasm_progress`
for the throttling and monotonic rules every report goes through.
"""

from __future__ import annotations

import io

from ada.cadit.wasm_progress import ConversionProgress, as_progress

# Mesh containers trimesh round-trips (glb is a valid source too: glb→obj/stl).
_MESH_EXTS = {"obj", "stl", "ply", "gltf", "dae", "off", "glb"}
_FEA_EXTS = {"rmed", "med", "sif", "sin", "sqlite"}
_DECK_TARGETS = {"inp", "fem", "med"}

# Where the stages of an ada-loaded conversion sit on the 0..1 bar: reading the source is
# one opaque step (the readers have no hook), tessellation counts objects, and the GLB
# write closes it.
_READ_END = 0.3
_TESS_END = 0.9

_FORMAT_LABELS = {
    "sat": "SAT",
    "ifc": "IFC",
    "step": "STEP",
    "stp": "STEP",
    "fem": "FEM deck",
    "genie": "Genie XML",
    "mesh": "mesh",
    "fea": "FEA result",
    "fea_glb": "FEA result",
}


def _label(fmt: str) -> str:
    return _FORMAT_LABELS.get((fmt or "").lower(), (fmt or "source").upper())


def _read_source(fmt: str, src: str, prog: ConversionProgress):
    """:func:`_load_assembly` between a "reading" and a "building scene" report."""
    prog(f"reading {_label(fmt)}", 0.02)
    asm = _load_assembly(fmt, src)
    prog("building scene", _READ_END)
    return asm


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


# The native NGEOM route: serializing each object's record (counted) fills the bar to here,
# then adacpp tessellates and writes the GLB in one call that reports nothing until it returns.
_RECORDS_END = 0.6

# The worker converter's stage codes, as the browser tells them.
_WORKER_STAGE_NAMES = {"parsing": "reading", "native-ngeom-tessellating": "tessellating (native)"}


def _worker_progress(prog: ConversionProgress, lo: float, hi: float):
    """The worker converter's ``on_progress(stage, fraction)`` onto ``[lo, hi]`` of the bar,
    its stage codes told the browser's way and its own ``"ready"`` dropped (:func:`run`
    closes with ``"done"``)."""
    scaled = prog.scaled(lo, hi)

    def _report(stage, fraction=0.0, *_a, **_k) -> None:
        stage = str(stage)
        if stage != "ready":
            scaled(_WORKER_STAGE_NAMES.get(stage, stage), fraction)

    return _report


def _assembly_glb(asm, prog: ConversionProgress | None = None) -> bytes:
    """GLB of a loaded Assembly through adapy's one GLB export path.

    :func:`ada.visit.glb_export.export_glb` is what the worker's converter (``_export_with_ada``)
    and ``to_gltf`` call too, with the same engine selection (libtess2 wherever adacpp imports)
    and the native NGEOM-record writer first, so the browser's GLB and the worker's agree by
    construction. A bare backend tessellation is not that: a thick curved shell went through the
    backend's face sewing, which kept one face of six and left the plate to a flat fallback.

    Progress: a per-object count either way -- the record writer's walk ("preparing geometry",
    then "tessellating (native)" for adacpp's one opaque call), or the scene writer's
    tessellation when the export takes that leg instead.
    """
    from ada.cadit.ngeom.export import report_record_progress
    from ada.visit.glb_export import export_glb
    from ada.visit.render_params import RenderParams
    from ada.visit.tessellate import report_tessellation_progress

    prog = as_progress(prog)
    counted = prog.counter("preparing geometry", _READ_END, _RECORDS_END, "objects")

    def records(done: int, total: int) -> None:
        # Every record serialized: adacpp's tessellate + GLB write runs next, in one call.
        if done >= total:
            prog("tessellating (native)", _RECORDS_END)
        else:
            counted(done, total)

    try:
        with report_tessellation_progress(_tessellation_reporter(prog)), report_record_progress(records):
            out = export_glb(asm, None, params=RenderParams(stream_from_ifc_store=False))
    except ValueError as exc:
        if _is_empty_scene(exc):
            raise RuntimeError(_NO_GEOMETRY_MSG) from exc
        raise
    prog("writing GLB", _TESS_END)
    return out


def _worker_mesh(src: str, fmt: str, target: str, prog: ConversionProgress | None = None) -> bytes:
    """OBJ/STL of a Genie/SAT source through the worker's own mesh leg.

    Not via :func:`_glb_to_mesh`: the worker's Genie GLB is EXT_meshopt-compressed, which
    trimesh cannot read back, and the worker writes these targets with its own mesh leg
    anyway (the native record writer for Genie, ``to_trimesh_scene`` otherwise).
    """
    import os
    import pathlib

    from ada.comms.rest.converters.mesh_step import _via_ada_to_trimesh

    prog = as_progress(prog)
    prog(f"reading {_label(fmt)}", 0.02)
    res = _via_ada_to_trimesh(
        pathlib.Path(src), _WORKER_GLB_SOURCE_EXT[fmt], target, _worker_progress(prog, 0.02, 0.98)
    )
    if isinstance(res, (bytes, bytearray)):
        return bytes(res)
    try:
        with open(res, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.remove(res)
        except OSError:
            pass


# Source formats whose OBJ/STL go through the worker's own mesh leg (their GLB: :func:`_assembly_glb`,
# :func:`_worker_mesh`), and the source extension those legs key their routes on.
_WORKER_GLB_SOURCE_EXT = {"genie": ".xml", "sat": ".sat"}


def _tessellation_reporter(prog: ConversionProgress):
    """The ``(done, total)`` tessellation callback: objects onto ``[_READ_END, _TESS_END]``,
    then "writing GLB" once every mesh is in the scene (the export runs after that)."""
    counted = prog.counter("tessellating", _READ_END, _TESS_END, "objects")

    def _report(done: int, total: int) -> None:
        if done >= total:
            prog("writing GLB", _TESS_END)
        else:
            counted(done, total)

    return _report


def _export_assembly(asm, target: str, fmt: str | None = None, prog: ConversionProgress | None = None) -> bytes:
    """Write an Assembly to a single-blob target."""
    target = target.lower()
    prog = as_progress(prog)
    if target == "glb":
        out = _assembly_glb(asm, prog)
    elif target in ("obj", "stl"):
        prog(f"writing {target.upper()}", _READ_END)
        try:
            data = asm.to_trimesh_scene().export(file_type=target)
        except ValueError as exc:
            if _is_empty_scene(exc):
                raise RuntimeError(_NO_GEOMETRY_MSG) from exc
            raise
        out = data.encode() if isinstance(data, str) else bytes(data)
    elif target in ("step", "stp"):
        prog("writing STEP", _READ_END)
        asm.to_stp("/tmp/_wasm_out.step")
        with open("/tmp/_wasm_out.step", "rb") as fh:
            out = fh.read()
    elif target == "xml":
        prog("writing Genie XML", _READ_END)
        asm.to_genie_xml("/tmp/_wasm_out.xml")
        with open("/tmp/_wasm_out.xml", "rb") as fh:
            out = fh.read()
    elif target == "gnx":
        # The Genie workspace: the same concept XML zipped with its SAT body,
        # pure python on top of the xml writer, so the browser can make it too.
        prog("writing Genie workspace", _READ_END)
        asm.to_gnx("/tmp/_wasm_out.gnx")
        with open("/tmp/_wasm_out.gnx", "rb") as fh:
            out = fh.read()
    elif target == "ifc":
        prog("writing IFC", _READ_END)
        asm.to_ifc("/tmp/_wasm_out.ifc")
        with open("/tmp/_wasm_out.ifc", "rb") as fh:
            out = fh.read()
    else:
        raise RuntimeError(f"wasm_convert: unsupported target {target!r}")
    if not out:
        raise RuntimeError(f"wasm_convert: produced an empty {target}")
    return out


def _step_to_glb_native(src: str) -> bytes | None:
    """STEP→GLB through the worker's default STEP leg (``adacpp-native``: adacpp's C++ STEP reader,
    libtess2 and the record GLB writer), or None where this adacpp lacks it."""
    import os

    from ada.cadit.step.native_step_to_glb import (
        native_adacpp_available,
        native_step_to_glb,
    )
    from ada.core.file_system import new_temp_path

    if not native_adacpp_available():
        return None
    out_path = new_temp_path(suffix=".glb")
    try:
        native_step_to_glb(src, out_path)
        with open(out_path, "rb") as fh:
            return fh.read()
    except Exception as exc:  # noqa: BLE001 - the OCAF path below is the fallback, as on the worker
        print(f"wasm_convert: native STEP->GLB failed ({exc!r}); using the OCAF path")
        return None
    finally:
        try:
            os.remove(out_path)
        except OSError:
            pass


def _step_to_glb(data: bytes) -> bytes:
    """STEP→GLB fallback: straight through the backend's OCAF pipeline (no ada loader).

    NOT ``read_step_bytes`` + ``write_glb_bytes``: those drop every colour between them
    (the plain STEPControl_Reader has no XCAF layer, so STYLED_ITEM is never read, and the
    writer builds a document with no ColorTool), yielding a GLB with zero materials that the
    viewer paints in its own default grey. ``step_bytes_to_glb_bytes`` keeps the OCAF document
    from reader to writer, so names, the assembly tree and colours survive into the materials.
    """
    import ada.cad

    backend = ada.cad.select_backend(prefer="adacpp")
    return backend.step_bytes_to_glb_bytes(data)


def _ifc_to_glb(src: str, prog: ConversionProgress | None = None) -> bytes:
    """IFC→GLB fast path: ifcopenshell geometry iterator → trimesh GLB."""
    import ifcopenshell
    import ifcopenshell.geom
    import numpy as np
    import trimesh

    prog = as_progress(prog)
    prog("reading IFC (ifcopenshell)", 0.02)
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
            try:
                # The iterator's own percentage of the products it walks.
                done = float(iterator.progress()) / 100.0
            except Exception:  # noqa: BLE001 - an iterator without progress() still converts
                done = 0.0
            prog("tessellating (ifcopenshell)", _READ_END + (_TESS_END - _READ_END) * done, f"{n} shapes")
            if not iterator.next():
                break
    if n == 0:
        raise RuntimeError("no meshable geometry produced from this IFC")
    prog("writing GLB", _TESS_END)
    buf = io.BytesIO()
    scene.export(buf, file_type="glb")
    return buf.getvalue()


def _to_glb(fmt: str, src: str, prog: ConversionProgress | None = None) -> bytes:
    """Produce GLB for a geometry source via its most reliable path."""
    prog = as_progress(prog)
    if fmt == "ifc":
        # adapy's own path first: it writes the scene-extras hierarchy
        # (``id_hierarchy`` + draw ranges) the selection tree and picking read, as
        # the server's IFC conversion does. The bare ifcopenshell iterator writes
        # none, so it is only the fallback for what adapy cannot load.
        try:
            return _export_assembly(_read_source("ifc", src, prog), "glb", "ifc", prog)
        except Exception as exc:  # noqa: BLE001 -- any adapy failure falls back
            print(f"wasm_convert: adapy IFC path failed ({exc!r}); using the ifcopenshell fast path")
            # The fallback starts over; its stages report on from where the bar is.
            return _ifc_to_glb(src, prog)
    if fmt in ("step", "stp"):
        # One call into adacpp (its native STEP->GLB, else its OCAF pipeline): it reports
        # nothing until it returns.
        prog("converting STEP → GLB (adacpp)", 0.05)
        native = _step_to_glb_native(src)
        if native is not None:
            return native
        with open(src, "rb") as fh:
            return _step_to_glb(fh.read())
    if fmt == "fem":
        # The worker's FEM->GLB draws the deck's mesh as read (``_load_with_ada``: no concept
        # objects; ``_apply_fem_to_objects`` skips mesh targets). Concept objects on top drew the
        # model twice over and named its elements differently from the worker's GLB.
        import ada

        prog(f"reading {_label(fmt)}", 0.02)
        asm = ada.from_fem(src)
        prog("building scene", _READ_END)
        return _export_assembly(asm, "glb", fmt, prog)
    return _export_assembly(_read_source(fmt, src, prog), "glb", fmt, prog)


def _without_meshopt(glb: bytes) -> bytes:
    """``glb`` with its EXT_meshopt_compression buffers decoded (the record writer's GLB carries
    them; trimesh cannot read them)."""
    import os
    import struct

    n = struct.unpack_from("<I", glb, 12)[0] if len(glb) >= 20 else 0
    if b"EXT_meshopt_compression" not in glb[20 : 20 + n]:
        return glb
    from ada.core.file_system import new_temp_path
    from ada.visit.gltf.meshopt import meshopt_decompress_glb

    src, dst = new_temp_path(suffix=".glb"), new_temp_path(suffix=".glb")
    try:
        with open(src, "wb") as fh:
            fh.write(glb)
        meshopt_decompress_glb(src, dst)
        with open(dst, "rb") as fh:
            return fh.read()
    finally:
        for p in (src, dst):
            try:
                os.remove(p)
            except OSError:
                pass


def _glb_to_mesh(glb: bytes, target: str, prog: ConversionProgress | None = None) -> bytes:
    """Convert in-memory GLB to obj/stl via trimesh.

    OBJ/STL are produced through GLB rather than asm.to_trimesh_scene() because
    the latter trips trimesh's OBJ exporter on Path3D (beam wireframe) geometry
    and yields an empty scene for sources whose shapes only tessellate on the
    GLB path (e.g. the STEP stream reader). Going via GLB makes obj/stl as
    reliable as glb itself.
    """
    import trimesh

    as_progress(prog)(f"writing {target.upper()}", 0.95)
    glb = _without_meshopt(glb)
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


def _convert_mesh(src: str, ext: str, target: str, prog: ConversionProgress | None = None) -> bytes:
    import trimesh

    prog = as_progress(prog)
    ext = ext.lower()
    target = (target or "glb").lower()
    prog(f"reading {ext.upper()} mesh", 0.05)
    loaded = trimesh.load(src, file_type=ext, process=False)
    scene = loaded if isinstance(loaded, trimesh.Scene) else trimesh.Scene(loaded)
    if len(scene.geometry) == 0:
        raise RuntimeError("no meshable geometry produced from this file")
    prog(f"writing {target.upper()}", 0.6)
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


def bake_fea(src: str, ext: str, src_key: str | None = None, on_progress=None) -> bytes:
    """FEA result → streaming-viewer artefact tree, returned as a zip.

    Registers the pure-Python stdlib ``sqlite3`` stream reader for ``.sqlite``
    databases (already-converted Abaqus results) so they bake in-browser
    without a native Abaqus exporter. Native ``.odb`` conversion remains
    server-worker only (``abaqus`` capability).

    ``src_key``: the source's storage key, recorded as the manifest's ``src`` as the worker's
    bake records it. Without one the manifest names the staged file's stem, which in the
    browser is a temporary name (``input``).
    """
    from ada.fem.results.artefacts import bake_fea_artefacts_from_source

    if (ext or "").lower().lstrip(".") == "sqlite":
        from ada.fem.formats.abaqus.results.sqlite_stream import (
            register as _register_sqlite,
        )

        # .sqlite only: the .odb reader shells out to ``abaqus python``, which
        # no browser has — registering it here would only defer the failure.
        _register_sqlite(odb=False)

    prog = as_progress(on_progress)
    prog("reading FEA result", 0.02)
    out_dir = _fresh_bake_dir()
    bake_fea_artefacts_from_source(
        src, out_dir, src_key=src_key or "", on_progress=prog.labelled("baking FEA artefacts", 0.1, 0.95)
    )
    prog("zipping artefacts", 0.96)
    return _zip_bake_dir(out_dir)


def fea_result_to_glb(src: str, on_progress=None) -> bytes:
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

    prog = as_progress(on_progress)
    prog("reading FEA result", 0.02)
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

    return _fea_result_glb_bytes(result, prog)


def _fea_result_glb_bytes(result, prog: ConversionProgress | None = None) -> bytes:
    """Tessellate a :class:`FEAResult`'s first (step, field) to a single GLB.
    Shared by the path-based and streamed SIN→GLB entrypoints."""
    as_progress(prog)("tessellating result mesh", 0.6)
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
    from ada.fem.formats.sesam.results.byte_source import PagedByteSource
    from ada.fem.formats.sesam.results.read_sin import (
        _read_streamed,
        stored_result_cases,
    )
    from ada.fem.formats.sesam.results.sin_reader import SinFile

    sin = SinFile(source=PagedByteSource(fetcher))
    try:
        if step is None and pick_first_step:
            steps = stored_result_cases(sin)
            if not steps:
                raise RuntimeError("SIN result has no RV* result steps")
            step = min(steps)
        return _read_streamed(sin, None if step is None else int(step), 0.0, None)
    finally:
        sin.close()


def _streamed_sin_bake(fetcher, src_key: str | None = None, prog: ConversionProgress | None = None) -> bytes:
    """Bake a SIN → streaming-viewer artefact tree (zip), reading the source
    over a range fetcher and **one step at a time**.

    Two compounding bounds keep this inside the wasm32 ceiling for an
    arbitrarily large deck: the source is range-streamed (the file never
    lands in wasm memory), and :class:`SinStreamReader` materialises only
    ~2 steps' worth of ``FEAResult`` at a time instead of the whole
    multi-step result. This buffered variant still gathers the whole
    artefact tree on MEMFS and zips it — see :func:`_streamed_sin_bake_to`
    for the per-file streaming-upload variant that bounds the output side
    too."""
    from ada.fem.formats.sesam.results.byte_source import PagedByteSource
    from ada.fem.formats.sesam.results.read_sin import SinStreamReader
    from ada.fem.results.artefacts import bake_artefacts

    prog = as_progress(prog)
    prog("reading FEA result (streamed)", 0.02)
    out_dir = _fresh_bake_dir()
    with SinStreamReader(PagedByteSource(fetcher)) as reader:
        bake_artefacts(
            reader, out_dir, src=src_key or "remote", on_progress=prog.labelled("baking FEA artefacts", 0.1, 0.95)
        )
    prog("zipping artefacts", 0.96)
    return _zip_bake_dir(out_dir)


def _streamed_sin_bake_to(fetcher, upload, src_key: str | None = None, prog: ConversionProgress | None = None) -> str:
    """Bake a SIN → artefact tree, shipping **each file as it lands** via
    ``upload(name, bytes)`` and freeing it from MEMFS immediately.

    The third compounding bound on top of :func:`_streamed_sin_bake`:
    range-streamed source + per-step result materialisation + **per-file
    output**. The tree never resides whole on MEMFS and is never zipped
    into a single in-memory buffer, so peak output memory is one artefact
    file (the largest single ``.bin``/``.glb``) rather than the whole tree
    plus its zip. ``upload`` is a host (JS) callback doing a synchronous
    POST of one artefact; it must raise on failure so the bake aborts
    rather than silently dropping a file.

    Returns a small JSON summary (file count + manifest filename) — there
    is no aggregate blob to hand back, the host already has every file.
    """
    import json
    import os

    from ada.fem.formats.sesam.results.byte_source import PagedByteSource
    from ada.fem.formats.sesam.results.read_sin import SinStreamReader
    from ada.fem.results.artefacts import bake_artefacts

    out_dir = _fresh_bake_dir()
    shipped: list[str] = []
    total_bytes = 0

    def _ship(path) -> None:
        nonlocal total_bytes
        name = os.path.basename(str(path))
        with open(path, "rb") as fh:
            data = fh.read()
        upload(name, data)
        shipped.append(name)
        total_bytes += len(data)
        # Free the artefact the instant it's shipped — the manifest is
        # built from in-memory metas, never re-reads the blob bytes.
        try:
            os.remove(path)
        except OSError:
            pass

    prog = as_progress(prog)
    prog("reading FEA result (streamed)", 0.02)
    with SinStreamReader(PagedByteSource(fetcher)) as reader:
        bake_artefacts(
            reader,
            out_dir,
            src=src_key or "remote",
            on_artefact=_ship,
            on_progress=prog.labelled("baking + uploading FEA artefacts", 0.1, 0.98),
        )

    if "fea.manifest.json" not in shipped:
        raise RuntimeError("FEA bake produced no manifest")
    return json.dumps({"count": len(shipped), "files": shipped, "manifest": "fea.manifest.json", "bytes": total_bytes})


def run_stream(
    fmt: str,
    ext: str,
    target: str,
    fetcher,
    step: int | None = None,
    upload=None,
    src_key: str | None = None,
    on_progress=None,
):
    """Streamed counterpart of :func:`run` — convert from a *range fetcher*
    rather than a fully-staged source file.

    Used by the browser worker for sources too large to hold in wasm memory:
    the host bridges an HTTP-``Range`` / ``fetch`` reader as ``fetcher``
    (``size()`` / ``fetch(offset, length) -> bytes``) and this streams over
    it. Supports ``fea_glb`` (Sesam ``.sin`` → single-step GLB) and ``fea``
    (``.sin`` → artefact-tree bake); other formats stay on the buffered
    :func:`run` path.

    For ``fea``: pass an ``upload(name, bytes)`` callback to stream each
    artefact file out as it bakes (returns a JSON summary string); omit it
    to get the whole tree back as a single zip (``bytes``). ``src_key``: the source's
    storage key, recorded as the manifest's ``src`` (see :func:`bake_fea`).

    ``on_progress(stage, fraction)``: told as the conversion goes (see :func:`run`).
    """
    fmt = (fmt or "").lower()
    target = (target or "glb").lower()
    prog = as_progress(on_progress)
    if fmt == "fea":
        if upload is not None:
            out = _streamed_sin_bake_to(fetcher, upload, src_key, prog)
        else:
            out = _streamed_sin_bake(fetcher, src_key, prog)
    elif fmt == "fea_glb":
        prog("reading FEA result (streamed)", 0.02)
        out = _fea_result_glb_bytes(_streamed_sin_result(fetcher, step=step, pick_first_step=True), prog)
    else:
        raise RuntimeError(f"wasm_convert: streaming source not supported for fmt={fmt!r} (only fea / fea_glb)")
    prog("done", 1.0)
    return out


def _run(fmt: str, ext: str, target: str, src: str, src_key: str | None, prog: ConversionProgress):
    if fmt == "fea":
        return bake_fea(src, ext, src_key, on_progress=prog)
    if fmt == "fea_glb":
        return fea_result_to_glb(src, on_progress=prog)
    if fmt == "mesh":
        return _convert_mesh(src, ext, target, prog)
    if fmt == "fem" and target in _DECK_TARGETS:
        from ada.fem.formats.deck_convert import fem_deck_to_bytes

        return fem_deck_to_bytes(src, target, on_progress=prog.scaled(0.0, 0.99))
    if target == "glb":
        return _to_glb(fmt, src, prog)
    if target in ("obj", "stl"):
        if fmt in _WORKER_GLB_SOURCE_EXT:
            return _worker_mesh(src, fmt, target, prog)
        # obj/stl via the reliable GLB tessellation path (see _glb_to_mesh).
        return _glb_to_mesh(_to_glb(fmt, src, prog), target, prog)
    # Structure-preserving writers (ifc / xml / step) keep the B-rep.
    return _export_assembly(_read_source(fmt, src, prog), target, prog=prog)


def run(
    fmt: str,
    ext: str,
    target: str,
    src: str,
    src_key: str | None = None,
    on_progress=None,
) -> bytes:
    """Convert ``src`` (a path on the pyodide FS) and return the output bytes.

    ``fmt`` is the wasm source-format class (sat/ifc/step/fem/genie/mesh/fea/
    fea_glb); ``ext`` the source file extension; ``target`` the requested
    output format. ``src_key``: the source's storage key, which an FEA bake records as its
    manifest's ``src`` (see :func:`bake_fea`); the other formats ignore it.

    ``on_progress(stage, fraction)``: told as the conversion goes -- reading the source,
    building the scene, tessellating (with an ``i/N objects`` count), writing the output,
    and ``("done", 1.0)`` last. Fractions only grow and reports within a stage are
    throttled (:class:`~ada.cadit.wasm_progress.ConversionProgress`), so a host can post
    each one straight to its page.
    """
    fmt = (fmt or "").lower()
    target = (target or "glb").lower()
    prog = as_progress(on_progress)
    out = _run(fmt, ext, target, src, src_key, prog)
    prog("done", 1.0)
    return out
