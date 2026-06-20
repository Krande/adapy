"""Server-side STEP->GLB pipeline backed by the step2glb engine.

step2glb (MIT, https://github.com/vegarringdal/step2glb) is a self-contained
Rust STEP->GLB converter with its own geometry kernel and tessellation
refinement. It renders surface types adapy's kernel-free stream reader skips
(rational B-spline, spherical, conical, toroidal) — the curved geometry that
otherwise vanishes from large assemblies.

Two engine choices matter for adapy and are pinned here so the GLB is
*self-sufficient for the existing viewer* (no frontend special-casing):

* ``--merged`` — one node/mesh per colour, geometry baked to world space, with
  per-part ``draw_ranges_node<matid>`` and the ``id_hierarchy`` in
  ``scenes[0].extras``. This is byte-for-byte the contract adapy's own
  ``write_glb_from_spill`` emits (the rvm layout ``prepareLoadedModel.ts``
  consumes), so picking + the model tree work with zero metadata post-pass. The
  alternative hierarchical output explodes into one glTF node per part (tens of
  thousands), which the viewer cannot batch — it hangs on load.

* ``--up-axis y`` — adapy keeps models in their native Z-up frame and does *not*
  rotate on GLB export; step2glb's default ``z`` rotates Z-up into glTF Y-up,
  which lands the model 90° off from every other adapy GLB. ``y`` means "input
  is already Y-up: no rotation", i.e. pass the coordinates through unchanged so
  the frame matches adapy exactly.

Why the CLI binary and not the in-process C ABI: merged mode holds all baked
geometry in RAM. Measured peak on a 0.8 GB / 300k-face assembly is ~3.0 GB
resident via the binary (which still spills the output chunk) — bounded enough
for a worker. The ``step2glb_capi`` shared library remains for small in-process
conversions and to document the C ABI.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

from ada.config import logger

_ENV_BIN = "ADAPY_STEP2GLB_BIN"
_BUNDLED = Path(__file__).with_name("_lib") / "step2glb"

_GLB_MAGIC = 0x46546C67
_CHUNK_JSON = 0x4E4F534A

# Spill the output chunk to disk above this; merged geometry itself stays
# resident (see module docstring), so this only bounds the final buffer.
_DEFAULT_MEMORY_THRESHOLD = "1gb"


class Step2GlbUnavailable(RuntimeError):
    """Raised when the step2glb binary cannot be located."""


def _resolve_bin() -> Path:
    env = os.environ.get(_ENV_BIN)
    candidates = [Path(env)] if env else []
    candidates.append(_BUNDLED)
    on_path = shutil.which("step2glb")
    if on_path:
        candidates.append(Path(on_path))
    for p in candidates:
        if p.exists():
            return p
    raise Step2GlbUnavailable(
        "step2glb binary not found. Set "
        f"{_ENV_BIN}=/path/to/step2glb, bundle it at {_BUNDLED}, or put it on PATH. "
        "Tried: " + "; ".join(str(c) for c in candidates)
    )


def is_available() -> bool:
    """True if the step2glb binary can be located (used to gate the pipeline/tests)."""
    try:
        _resolve_bin()
        return True
    except Step2GlbUnavailable:
        return False


def _read_glb_json(glb_path: Path) -> dict:
    """Read just the JSON chunk of a GLB (the BIN chunk is never loaded)."""
    with open(glb_path, "rb") as f:
        magic, _version, _total = struct.unpack("<III", f.read(12))
        if magic != _GLB_MAGIC:
            raise ValueError(f"{glb_path} is not a GLB (bad magic)")
        json_len, json_type = struct.unpack("<II", f.read(8))
        if json_type != _CHUNK_JSON:
            raise ValueError(f"{glb_path}: first chunk is not JSON")
        return json.loads(f.read(json_len))


def _assert_viewer_contract(glb_path: Path) -> None:
    """Fail loudly if the GLB lacks the adapy viewer picking/tree contract.

    Guards against an upstream step2glb change silently dropping the merged
    ``scenes[0].extras`` (id_hierarchy + draw_ranges) — which would degrade the
    viewer to whole-mesh picking with no tree, hard to spot in a 1 GB binary.
    """
    tree = _read_glb_json(glb_path)
    scenes = tree.get("scenes") or [{}]
    extras = scenes[0].get("extras") or {}
    if "id_hierarchy" not in extras:
        raise RuntimeError(
            f"{glb_path}: missing scenes[0].extras.id_hierarchy — step2glb --merged "
            "did not emit the viewer contract (output layout changed?)"
        )
    if not any(k.startswith("draw_ranges_node") for k in extras):
        raise RuntimeError(f"{glb_path}: no draw_ranges_node* in scene extras")


def convert_step_to_glb(
    step_path: str | Path,
    glb_path: str | Path,
    *,
    deflection_mm: float = 1.0,
    max_angle_deg: float = 25.0,
    memory_threshold: str = _DEFAULT_MEMORY_THRESHOLD,
    up_axis: str = "y",
) -> Path:
    """Convert a STEP file to a viewer-ready GLB via the step2glb binary.

    Runs the binary in ``--merged`` mode (adapy's native picking/tree contract,
    bounded RAM) with ``--up-axis y`` (no rotation; matches adapy's Z-up frame),
    then verifies the GLB carries the viewer contract. The result needs no
    frontend-side special-casing.
    """
    binary = _resolve_bin()
    step_path = Path(step_path)
    glb_path = Path(glb_path)
    glb_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="step2glb_") as td:
        raw = Path(td) / "out.glb"
        cmd = [
            str(binary),
            str(step_path),
            "-o",
            str(raw),
            "--merged",
            "--up-axis",
            up_axis,
            "--memory-threshold",
            memory_threshold,
            "--deflection",
            str(deflection_mm),
            "--max-angle",
            str(max_angle_deg),
        ]
        logger.info("step2glb: %s", " ".join(cmd))
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"step2glb failed (rc={proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}"
            )
        if not raw.exists():
            raise RuntimeError("step2glb reported success but produced no GLB")
        _assert_viewer_contract(raw)
        shutil.move(str(raw), str(glb_path))
    return glb_path
