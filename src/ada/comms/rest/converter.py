"""Source-to-GLB converter for the hosted viewer.

Synchronous, stateless function: takes the raw bytes of a source file
(IFC, STEP, FEM input deck, etc.) and returns GLB bytes. The worker
process runs this in a threadpool so it doesn't block the asyncio loop.

Format dispatch is by file extension. Pure pass-through for files
already in GLB / glTF / OBJ / STL / PLY (trimesh handles the latter
three natively, no ada-py round-trip needed). Everything else goes
through ada.from_<format> -> Assembly -> to_gltf.

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

# Extensions we just pass through unchanged.
_PASSTHROUGH_EXTS: frozenset[str] = frozenset({".glb"})


def _ext(key: str) -> str:
    return pathlib.PurePosixPath(key).suffix.lower()


def derived_key_for(source_key: str) -> str:
    """Map a source key to its derived GLB key.

    Convention: the derived path mirrors the source path under the
    `_derived/` prefix, with `.glb` appended so the derived key is
    unambiguous even when two sources differ only by extension
    (e.g. `wall.ifc` vs `wall.glb`).
    """
    src = source_key.strip("/")
    return f"_derived/{src}.glb"


def is_derived_key(key: str) -> bool:
    return key.lstrip("/").startswith("_derived/")


def is_supported_source(key: str) -> bool:
    ext = _ext(key)
    return (
        ext in _PASSTHROUGH_EXTS
        or ext in _TRIMESH_EXTS
        or ext in {".gltf", ".ifc", ".step", ".stp", ".xml", ".inp", ".fem", ".sat", ".acis"}
    )


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


def _via_ada(data: bytes, ext: str, on_progress: ProgressFn) -> bytes:
    """Heavy path: write source bytes to a temp file, load with ada,
    export GLB to memory. Used for IFC / STEP / FEM input decks /
    Genie XML / ACIS — anything that needs the full ada-py stack.
    """
    import ada

    on_progress("staging", 0.05)
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(data)
        src_path = pathlib.Path(tmp.name)

    try:
        on_progress("parsing", 0.15)
        if ext == ".ifc":
            model = ada.from_ifc(src_path)
        elif ext in {".step", ".stp"}:
            model = ada.from_step(src_path)
        elif ext == ".xml":
            model = ada.from_genie_xml(src_path)
        elif ext in {".inp", ".fem"}:
            model = ada.from_fem(src_path)
        elif ext in {".sat", ".acis"}:
            model = ada.from_acis(src_path)
        else:
            raise UnsupportedFormat(f"ada path does not handle {ext!r}")

        on_progress("tessellating", 0.55)
        out = io.BytesIO()
        model.to_gltf(out)
        on_progress("ready", 1.0)
        return out.getvalue()
    finally:
        try:
            src_path.unlink()
        except OSError:
            pass


def convert_to_glb(
    data: bytes,
    source_key: str,
    on_progress: ProgressFn | None = None,
) -> bytes:
    """Convert raw source bytes to GLB bytes. See module docstring."""
    progress = on_progress or (lambda _stage, _frac: None)
    progress("starting", 0.0)

    ext = _ext(source_key)
    if not ext:
        raise UnsupportedFormat(f"missing extension on key {source_key!r}")

    if ext in _PASSTHROUGH_EXTS:
        return _passthrough(data, progress)

    if ext == ".gltf":
        return _via_gltf_to_glb(data, progress)

    if ext in _TRIMESH_EXTS:
        return _via_trimesh(data, ext, progress)

    return _via_ada(data, ext, progress)


def supported_extensions() -> Iterable[str]:
    return sorted(
        _PASSTHROUGH_EXTS
        | _TRIMESH_EXTS
        | {".gltf", ".ifc", ".step", ".stp", ".xml", ".inp", ".fem", ".sat", ".acis"}
    )
