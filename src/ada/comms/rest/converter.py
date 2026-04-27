"""Source-to-target format converter for the hosted viewer.

Synchronous, stateless function: takes the raw bytes of a source file
(IFC, STEP, FEM input deck, etc.) and returns the bytes of the
requested target format. The worker process runs this in a threadpool
so it doesn't block the asyncio loop.

Two flavors of target:

* GLB / glTF — for the in-browser viewer. Direct GLB pass-through;
  trimesh handles OBJ / STL / PLY / DAE / OFF / glTF; everything else
  goes through ada.from_<format> -> Assembly -> to_gltf.

* Non-GLB (IFC, Genie XML) — for user download only. Source must be
  ada-loadable so we can build an Assembly first, then export via the
  matching writer (model.to_ifc / model.to_genie_xml).

The on_progress callback is invoked at named stages so the worker can
update the queue's progress field; values are best-effort estimates,
not measured ratios.
"""

from __future__ import annotations

import io
import pathlib
import tempfile
from typing import Callable, Iterable

# Progress contract: stage name (str), fraction (0..1).
ProgressFn = Callable[[str, float], None]


class UnsupportedFormat(ValueError):
    pass


# Extensions we hand to trimesh directly. trimesh will infer the type
# and emit GLB without ada-py needing to be involved at all.
_TRIMESH_EXTS: frozenset[str] = frozenset({".obj", ".stl", ".ply", ".dae", ".off"})

# Extensions we just pass through unchanged (only meaningful for GLB target).
_PASSTHROUGH_EXTS: frozenset[str] = frozenset({".glb"})

# Source formats that ada-py can load. Required for any non-GLB target.
_ADA_LOADABLE_EXTS: frozenset[str] = frozenset(
    {".ifc", ".step", ".stp", ".xml", ".inp", ".fem", ".sat", ".acis"}
)

# Allowed target formats. Each value is the file extension (with dot)
# of the produced bytes.
TARGET_FORMATS: frozenset[str] = frozenset({"glb", "ifc", "xml"})


def _ext(key: str) -> str:
    return pathlib.PurePosixPath(key).suffix.lower()


def derived_key_for(source_key: str, target_format: str = "glb") -> str:
    """Map a source key to its derived blob key.

    Convention: derived path mirrors the source path under `_derived/`,
    with `.{target_format}` appended so multiple targets coexist for
    the same source (`_derived/wall.ifc.glb`, `_derived/wall.ifc.xml`,
    ...).
    """
    fmt = target_format.lstrip(".").lower()
    if fmt not in TARGET_FORMATS:
        raise UnsupportedFormat(f"unknown target format: {target_format!r}")
    src = source_key.strip("/")
    return f"_derived/{src}.{fmt}"


def is_derived_key(key: str) -> bool:
    return key.lstrip("/").startswith("_derived/")


def is_supported_source(key: str) -> bool:
    ext = _ext(key)
    return (
        ext in _PASSTHROUGH_EXTS
        or ext in _TRIMESH_EXTS
        or ext in {".gltf"}
        or ext in _ADA_LOADABLE_EXTS
    )


def supported_targets_for(source_key: str) -> list[str]:
    """Return the target formats viable for a given source key. Used
    by the frontend to render only the conversion options that will
    actually succeed."""
    ext = _ext(source_key)
    targets: list[str] = []
    if ext in _PASSTHROUGH_EXTS or ext in _TRIMESH_EXTS or ext == ".gltf":
        targets.append("glb")
    if ext in _ADA_LOADABLE_EXTS:
        # ada-loadable sources can produce any target.
        targets = ["glb", "ifc", "xml"]
    return targets


def _passthrough(data: bytes, on_progress: ProgressFn) -> bytes:
    on_progress("ready", 1.0)
    return data


def _via_trimesh(data: bytes, ext: str, on_progress: ProgressFn) -> bytes:
    import trimesh

    on_progress("loading", 0.2)
    scene = trimesh.load(io.BytesIO(data), file_type=ext.lstrip("."))
    on_progress("exporting", 0.8)
    out = io.BytesIO()
    scene.export(file_obj=out, file_type="glb")
    on_progress("ready", 1.0)
    return out.getvalue()


def _via_gltf_to_glb(data: bytes, on_progress: ProgressFn) -> bytes:
    """glTF (text JSON) → GLB (binary). trimesh handles this round-trip."""
    return _via_trimesh(data, ".gltf", on_progress)


def _load_with_ada(src_path: pathlib.Path, ext: str):
    import ada

    if ext == ".ifc":
        return ada.from_ifc(src_path)
    if ext in {".step", ".stp"}:
        return ada.from_step(src_path)
    if ext == ".xml":
        return ada.from_genie_xml(src_path)
    if ext in {".inp", ".fem"}:
        return ada.from_fem(src_path)
    if ext in {".sat", ".acis"}:
        return ada.from_acis(src_path)
    raise UnsupportedFormat(f"ada path does not handle {ext!r}")


def _export_with_ada(model, target_format: str, out_path: pathlib.Path, on_progress: ProgressFn) -> bytes:
    """Run the matching ada exporter and read back the produced bytes."""
    if target_format == "glb":
        on_progress("tessellating", 0.55)
        buf = io.BytesIO()
        model.to_gltf(buf)
        on_progress("ready", 1.0)
        return buf.getvalue()
    if target_format == "ifc":
        on_progress("writing-ifc", 0.55)
        model.to_ifc(destination=str(out_path))
    elif target_format == "xml":
        on_progress("writing-xml", 0.55)
        model.to_genie_xml(destination_xml=str(out_path))
    else:
        raise UnsupportedFormat(f"unknown target format: {target_format!r}")
    on_progress("ready", 1.0)
    return out_path.read_bytes()


def _via_ada(data: bytes, source_ext: str, target_format: str, on_progress: ProgressFn) -> bytes:
    """Heavy path: write source bytes to a temp file, load with ada,
    export to target format. Used for any non-trivial source/target
    combination that needs the full ada-py stack."""
    on_progress("staging", 0.05)
    suffix = ".glb" if target_format == "glb" else f".{target_format}"
    with tempfile.NamedTemporaryFile(delete=False, suffix=source_ext) as src_tmp:
        src_tmp.write(data)
        src_path = pathlib.Path(src_tmp.name)
    out_path = pathlib.Path(tempfile.mkstemp(suffix=suffix)[1])

    try:
        on_progress("parsing", 0.15)
        model = _load_with_ada(src_path, source_ext)
        return _export_with_ada(model, target_format, out_path, on_progress)
    finally:
        for p in (src_path, out_path):
            try:
                p.unlink()
            except OSError:
                pass


def convert(
    data: bytes,
    source_key: str,
    target_format: str = "glb",
    on_progress: ProgressFn | None = None,
) -> bytes:
    """Convert raw source bytes to the requested target format. See module docstring."""
    progress = on_progress or (lambda _stage, _frac: None)
    progress("starting", 0.0)

    fmt = target_format.lstrip(".").lower()
    if fmt not in TARGET_FORMATS:
        raise UnsupportedFormat(f"unknown target format: {target_format!r}")

    src_ext = _ext(source_key)
    if not src_ext:
        raise UnsupportedFormat(f"missing extension on key {source_key!r}")

    if fmt == "glb":
        if src_ext in _PASSTHROUGH_EXTS:
            return _passthrough(data, progress)
        if src_ext == ".gltf":
            return _via_gltf_to_glb(data, progress)
        if src_ext in _TRIMESH_EXTS:
            return _via_trimesh(data, src_ext, progress)
        return _via_ada(data, src_ext, "glb", progress)

    # Non-GLB targets require an ada-loadable source.
    if src_ext not in _ADA_LOADABLE_EXTS:
        raise UnsupportedFormat(
            f"target {fmt!r} requires an ada-loadable source; got {src_ext!r}"
        )
    return _via_ada(data, src_ext, fmt, progress)


# Backwards-compat shim: existing callers import convert_to_glb.
def convert_to_glb(
    data: bytes,
    source_key: str,
    on_progress: ProgressFn | None = None,
) -> bytes:
    return convert(data, source_key, "glb", on_progress)


def supported_extensions() -> Iterable[str]:
    return sorted(
        _PASSTHROUGH_EXTS
        | _TRIMESH_EXTS
        | {".gltf"}
        | _ADA_LOADABLE_EXTS
    )
