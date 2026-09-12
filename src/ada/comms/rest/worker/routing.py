"""The misroute guard: can this pool serve the job NATS just handed it?

Routing happens at the subject layer (each pool subscribes to its own capability-suffixed
subject), so a job arriving at a pool should always be one it can handle. When it is not (a
routing bug, or a job enqueued before an upgrade) the loop fails it at once with a reason
naming the real problem, instead of NAK-looping until the delivery budget is spent.

Synthetic kinds have no source file, so the extension check does not apply to them. That set
comes from the format registry rather than a hand-kept list: the list once omitted
``procedural_engine_build``, whose synthetic key has no extension, so every engine-build job
was failed as misrouted on every pool.
"""

from __future__ import annotations

import pathlib

from ..converter import LEGACY_CONVERT_EXTS
from ..formats.registry import synthetic_kinds


def misroute_reason(
    target_format: str,
    source_key: str,
    *,
    cap: str,
    source_ext_set: set[str] | frozenset[str],
    ext_allow_set: set[str] | frozenset[str] | None,
) -> str | None:
    """``None`` when the pool can serve the job, else the message to fail it with."""
    # Importing the package populates the registry; the registry module itself is a leaf.
    import ada.comms.rest.formats  # noqa: F401

    if target_format in synthetic_kinds():
        return None
    ext = pathlib.PurePosixPath(source_key).suffix.lower()
    legacy_ok = ext in LEGACY_CONVERT_EXTS and (ext_allow_set is None or ext in ext_allow_set)
    if ext in source_ext_set or legacy_ok:
        return None
    return (
        f"misrouted: pool capability {cap!r} "
        f"can't handle .{ext.lstrip('.')} "
        f"(supported here: {sorted(source_ext_set) or ['legacy convert']})"
    )
