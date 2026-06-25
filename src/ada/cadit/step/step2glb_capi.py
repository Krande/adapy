"""ctypes binding for the step2glb Rust engine (STEP bytes -> GLB bytes).

step2glb (MIT, https://github.com/vegarringdal/step2glb) is a self-contained
STEP->GLB converter with its own geometry kernel and tessellation refinement.
It handles surface types adapy's kernel-free stream reader skips (rational
B-spline, spherical, conical, toroidal), so it is offered as an alternative
server-side STEP->GLB pipeline (see ``ADAPY_STEP_GLB_PIPELINE`` /
``step2glb_to_glb``).

Only the C ABI is used — no Rust toolchain is needed at adapy build/run time, just
the prebuilt shared library located via ``ADAPY_STEP2GLB_LIB`` (or bundled next to
this module). The ABI is two functions::

    int  step2glb_convert(const uint8_t* in, size_t in_len,
                          uint8_t** out, size_t* out_len);   // 0 ok, 1 bad-args, 2 convert-fail
    void step2glb_free(uint8_t* ptr, size_t len);

The library is loaded lazily and memoized; importing this module never requires
the ``.so`` to be present. ``convert_step_bytes_to_glb_bytes`` raises a clear
error when the library is requested but missing, so an explicit
``ADAPY_STEP_GLB_PIPELINE=step2glb`` selection fails loudly instead of silently
degrading.
"""

from __future__ import annotations

import ctypes
import os
from functools import lru_cache
from pathlib import Path

from ada.config import logger

_ENV_LIB = "ADAPY_STEP2GLB_LIB"
_BUNDLED = Path(__file__).with_name("_lib") / "libstep2glb_capi.so"


class Step2GlbUnavailable(RuntimeError):
    """Raised when the step2glb shared library cannot be located or loaded."""


def _candidate_paths() -> list[Path]:
    paths: list[Path] = []
    env = os.environ.get(_ENV_LIB)
    if env:
        paths.append(Path(env))
    paths.append(_BUNDLED)
    return paths


@lru_cache(maxsize=1)
def _load() -> ctypes.CDLL:
    tried: list[str] = []
    for p in _candidate_paths():
        if not p.exists():
            tried.append(f"{p} (missing)")
            continue
        try:
            lib = ctypes.CDLL(str(p))
        except OSError as exc:  # malformed / wrong-arch / unresolved deps
            tried.append(f"{p} ({exc})")
            continue
        lib.step2glb_convert.restype = ctypes.c_int
        lib.step2glb_convert.argtypes = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)),
            ctypes.POINTER(ctypes.c_size_t),
        ]
        lib.step2glb_free.restype = None
        lib.step2glb_free.argtypes = [ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t]
        logger.debug("loaded step2glb library from %s", p)
        return lib
    raise Step2GlbUnavailable(
        "step2glb shared library not found. Set "
        f"{_ENV_LIB}=/path/to/libstep2glb_capi.so or bundle it at {_BUNDLED}. "
        "Tried: " + "; ".join(tried)
    )


def is_available() -> bool:
    """True if the step2glb library can be loaded (used to gate the pipeline/tests)."""
    try:
        _load()
        return True
    except Step2GlbUnavailable:
        return False


def convert_step_bytes_to_glb_bytes(step: "bytes | bytearray | memoryview") -> bytes:
    """Convert STEP bytes to GLB bytes via the step2glb engine.

    The returned heap buffer is copied into a Python ``bytes`` and then always
    released with ``step2glb_free`` — the GLB never outlives the Rust allocation.

    Pass a writable buffer (``bytearray``/``mmap``) to avoid an extra Python-side
    copy of large inputs: a writable buffer is borrowed in place via ``from_buffer``,
    whereas an immutable ``bytes`` must be copied once with ``from_buffer_copy`` (it
    cannot back a mutable ctypes array). The Rust side copies the input internally
    regardless, so a writable buffer keeps peak RAM at one extra copy, not two.
    """
    lib = _load()
    in_len = len(step)
    if in_len == 0:
        in_ptr = (ctypes.c_uint8 * 0)()
    else:
        arr_t = ctypes.c_uint8 * in_len
        try:
            in_ptr = arr_t.from_buffer(step)  # writable buffer: no copy
        except TypeError:
            in_ptr = arr_t.from_buffer_copy(step)  # immutable bytes: one copy

    out_ptr = ctypes.POINTER(ctypes.c_uint8)()
    out_len = ctypes.c_size_t(0)
    rc = lib.step2glb_convert(in_ptr, in_len, ctypes.byref(out_ptr), ctypes.byref(out_len))
    if rc != 0:
        reason = {1: "bad arguments", 2: "conversion failure"}.get(rc, f"rc={rc}")
        raise RuntimeError(f"step2glb_convert failed ({reason})")
    try:
        return ctypes.string_at(out_ptr, out_len.value)
    finally:
        lib.step2glb_free(out_ptr, out_len)


def convert_step_file_to_glb_bytes(step_path: str | Path) -> bytes:
    """Read a STEP file and convert it to GLB bytes.

    Reads into a preallocated ``bytearray`` with ``readinto`` (a single disk->RAM
    copy) so the buffer is borrowed in place by ctypes rather than copied again —
    matters for the ~0.8 GB CAD assemblies this pipeline targets.
    """
    p = Path(step_path)
    buf = bytearray(p.stat().st_size)
    with open(p, "rb") as fh:
        fh.readinto(buf)
    return convert_step_bytes_to_glb_bytes(buf)
