"""The quantity take-off a conversion computes on its way past the structured model.

WHY IT IS SPLIT IN TWO. The take-off is a fact about the MODEL -- mass, centre of gravity, beams
by section, plates by thickness -- and a GLB carries only triangles, so it can only be computed
where an ada ``Part`` still exists: inside the exporter, for as long as it takes to tessellate.
But the file it belongs beside is the conversion child's RESULT path, which the exporter never
sees (it hands its output back as bytes and the child writes them somewhere else entirely). So
the exporter RECORDS and the child WRITES -- the same shape the child already uses to emit the
tessellation tallies it consumes from module state after the handler returns.

Without this the viewer's `Stats` and `Take-off` panels were empty for every uploaded file: the
take-off existed only for models the procedural engine compiled, though an ordinary IFC
conversion holds exactly the same model in its hands.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

from ada.config import logger

from .keys import stats_sidecar_key

__all__ = [
    "SOURCE_SIZE_LIMIT_ENV",
    "consume_takeoff",
    "record_takeoff",
    "record_takeoff_from_source",
    "source_is_small_enough",
    "write_takeoff_sidecar",
]

#: How large a source may be before a NATIVE conversion stops reading it a second time just to
#: take it off. Bytes; ``0`` disables the second read entirely.
SOURCE_SIZE_LIMIT_ENV = "ADA_TAKEOFF_MAX_SOURCE_BYTES"
_DEFAULT_SOURCE_LIMIT = 25 * 1024 * 1024

#: Set by the exporter, consumed by the conversion child. Process-local by construction: the
#: child is a fork, so one conversion's take-off can never be read by another's.
_PENDING: dict[str, Any] | None = None


def record_takeoff(model) -> None:
    """Compute the take-off for ``model`` and hold it for the child to write.

    BEST EFFORT, ALWAYS. A source that yields no ``Part`` (a streamed STEP, an FEA result) and a
    take-off that raised both record nothing, and the panel then says the take-off is
    unavailable -- which is true. Statistics are a nicety; they are never a reason for a
    conversion to fail.
    """
    global _PENDING

    from ada import Assembly, Part

    _PENDING = None
    if not isinstance(model, (Part, Assembly)):
        return
    try:
        from ada.topo_model.takeoff import model_takeoff

        _PENDING = model_takeoff(model, source_name=getattr(model, "name", None))
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.warning(f"take-off for {getattr(model, 'name', model)!r} failed, no stats sidecar: {exc}")
        _PENDING = None


def consume_takeoff() -> dict[str, Any] | None:
    """Take the recorded take-off, leaving nothing behind."""
    global _PENDING

    pending, _PENDING = _PENDING, None
    return pending


def write_takeoff_sidecar(result_path: "pathlib.Path | str") -> pathlib.Path | None:
    """Write whatever was recorded beside ``result_path``; return the file, or None.

    The sibling rule is ``stats_sidecar_key``'s, the same one a compiled procedural model's
    take-off follows, so the viewer asks one question of both.
    """
    stats = consume_takeoff()
    if stats is None:
        return None
    path = pathlib.Path(stats_sidecar_key(str(result_path)))
    try:
        path.write_text(json.dumps(stats), encoding="utf-8")
    except OSError as exc:
        logger.warning(f"could not write the take-off sidecar next to {result_path}: {exc}")
        return None
    return path


def source_is_small_enough(src_path: "pathlib.Path | str") -> bool:
    """Whether a take-off is worth a SECOND read of ``src_path``.

    The native IFC and STEP routes never build an ada model -- that is the point of them, and why
    they convert a plant in seconds. Taking one off therefore costs a full semantic read on top
    of a conversion that deliberately avoided one, which is minutes and gigabytes on the files
    those routes exist for. Only sources that need that read reach here: an IFC the native member
    reader can take off is answered before this is asked. So the FULL read is bounded by source
    size (25 MB by default,
    ``ADA_TAKEOFF_MAX_SOURCE_BYTES`` to change it, ``0`` to switch it off): ordinary models get
    their take-off, and a plant-scale conversion is not quietly doubled for a panel nobody may
    open.
    """
    import os

    raw = (os.environ.get(SOURCE_SIZE_LIMIT_ENV) or "").strip()
    try:
        limit = int(raw) if raw else _DEFAULT_SOURCE_LIMIT
    except ValueError:
        limit = _DEFAULT_SOURCE_LIMIT
    if limit <= 0:
        return False
    try:
        return pathlib.Path(src_path).stat().st_size <= limit
    except OSError:
        return False


def record_takeoff_from_source(src_path: "pathlib.Path | str", source_ext: str) -> None:
    """Read ``src_path`` purely to take it off -- the native routes' only option.

    Two ways in. An IFC is read NATIVELY first (`native_takeoff_part`), which is the cheap one:
    members, sections and materials straight out of the entities that state them, no
    ifcopenshell and no geometry. It is also the unbounded one -- the read it replaces is the
    reason the bound exists, so a source that answers natively is never measured against it, and
    a plant-scale IFC gets the take-off it was previously too large for.

    Everything else -- a STEP, or an IFC the native reader will not vouch for (see
    `native_takeoff_part`) -- still pays for the full reader, and so is still bounded by
    :func:`source_is_small_enough`. Best effort throughout: a read that fails leaves no take-off
    and the panel says so.
    """
    try:
        from ada.cadit.ifc.read.native_members import native_takeoff_part

        part = native_takeoff_part(src_path, source_ext)
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.info(f"native take-off of {src_path} unavailable ({exc}); falling back")
        part = None
    if part is not None:
        record_takeoff(part)
        return

    if not source_is_small_enough(src_path):
        logger.info(f"take-off skipped for {src_path}: source over {SOURCE_SIZE_LIMIT_ENV}")
        return
    try:
        from .ada_load import _load_with_ada

        record_takeoff(_load_with_ada(pathlib.Path(src_path), source_ext))
    except Exception as exc:  # noqa: BLE001 - a take-off is never a reason to fail a conversion
        logger.warning(f"take-off read of {src_path} failed: {exc}")
