"""Build orchestration for adapy-driven projects.

Lets a downstream project declare which files an entrypoint produces.
The ``ada-build`` CLI sets up a build context, calls the entrypoint,
collects whatever it ``publish()``-es, then computes git provenance and
(eventually) uploads to adapy-viewer.

Outside an active ``ada-build`` context, ``publish()`` is a no-op with a
warning so entrypoints stay runnable as plain scripts during dev.
"""
from __future__ import annotations

import logging
import pathlib
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

_FORMAT_BY_SUFFIX = {
    ".glb": "glb",
    ".gltf": "gltf",
    ".ifc": "ifc",
    ".json": "json",
    ".step": "step",
    ".stp": "step",
}


@dataclass
class BuildArtefact:
    path: pathlib.Path
    name: str
    format: str


@dataclass
class BuildContext:
    project_id: str
    entrypoint_name: str
    output_dir: pathlib.Path
    artefacts: list[BuildArtefact] = field(default_factory=list)


_active_context: ContextVar[Optional[BuildContext]] = ContextVar(
    "ada_build_context", default=None
)


def current_context() -> Optional[BuildContext]:
    return _active_context.get()


def publish(
    path: str | pathlib.Path,
    *,
    name: Optional[str] = None,
    format: str = "auto",
) -> None:
    """Register a file as a build artefact for upload by ada-build.

    Call after the entrypoint has written the file. ``name`` falls back
    to the file's basename; ``format`` is inferred from the suffix.
    Outside an ``ada-build`` run the call is ignored with a warning so
    the same entrypoint stays usable as a plain script.
    """
    ctx = _active_context.get()
    p = pathlib.Path(path)

    if ctx is None:
        logger.warning(
            "ada.build.publish(%s) called outside an ada-build context — "
            "ignored. Run via `ada-build run` to activate.",
            p,
        )
        return

    if not p.exists():
        raise FileNotFoundError(f"publish() called with non-existent path: {p}")

    fmt = format
    if fmt == "auto":
        fmt = _FORMAT_BY_SUFFIX.get(p.suffix.lower(), p.suffix.lstrip(".") or "bin")

    artefact_name = name or p.name
    ctx.artefacts.append(BuildArtefact(path=p, name=artefact_name, format=fmt))
    logger.info(
        "Registered artefact: %s (format=%s, source=%s)", artefact_name, fmt, p
    )
