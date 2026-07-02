"""Shared per-solid STEP reader for the streaming non-glb writers.

Prefer the fast native adacpp ``StepNgeomStream`` reader; fall back to the
pure-Python stream reader. The native hydrate path (native NGEOM buffer ->
``deserialize_geometries`` -> ``ada.geom``) can still misdecode some complex
geometry (a cyclic/garbled buffer surfaces as a ``RecursionError`` or
``NgeomDecodeError``). ``read_solids`` probes the native reader's first solid and,
if it fails, transparently restarts the WHOLE file on the pure-Python reader — so
native can be preferred without risking a hard conversion failure or lost geometry.
"""

from __future__ import annotations

import pathlib
from typing import Iterator

from ada.cadit.ngeom.deserialize import NgeomDecodeError
from ada.config import logger
from ada.geom import Geometry

# A native NGEOM hydrate failure that means "fall back to pure-Python for this file".
NATIVE_DECODE_ERRORS = (NgeomDecodeError, RecursionError)


def native_available() -> bool:
    from ada.cadit.step.read.native_reader import native_adacpp_step_available

    return native_adacpp_step_available()


def _python_solids(src_path) -> Iterator[Geometry]:
    # local_pool=False: random-access two-pass index (valid for any reference order);
    # tolerant skips unsupported solids rather than raising.
    from ada.cadit.step.read.stream_reader import stream_read_step

    yield from stream_read_step(src_path, local_pool=False, tolerant=True)


def read_solids(src_path: str | pathlib.Path) -> Iterator[Geometry]:
    """Yield one ``ada.geom.Geometry`` per solid. Uses the native reader when it
    decodes cleanly; otherwise falls back to the pure-Python reader for the file."""
    if not native_available():
        yield from _python_solids(src_path)
        return

    from ada.cadit.step.read.native_reader import native_stream_read_step

    gen = native_stream_read_step(src_path)
    # Probe the first solid: native decode failures show up here for files whose
    # geometry the hydrate path can't handle (the failing record kinds recur, so a
    # file that decodes its first solid reliably decodes the rest).
    try:
        first = next(gen)
    except StopIteration:
        return
    except NATIVE_DECODE_ERRORS as exc:
        logger.warning("native STEP reader failed (%s); falling back to pure-Python for %s", exc, src_path)
        yield from _python_solids(src_path)
        return

    yield first
    try:
        yield from gen
    except NATIVE_DECODE_ERRORS as exc:
        # A mid-stream native failure can't be un-yielded; surface it so the caller
        # fails loudly rather than silently truncating (rare — first-solid probe
        # catches the common case).
        raise NgeomDecodeError(f"native STEP reader failed mid-stream: {exc}") from exc
