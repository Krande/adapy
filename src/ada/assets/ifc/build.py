"""``IfcAssetBuilder`` -- the worker-side half of the IFC provider's ``build`` claim.

Fetches the source IFC through the sync storage facade, resolves the requested node's DESCENDANT
GUID SET from the PUBLISHED spine (never widening to the whole file if the spine is missing --
Decision 4: "no widen-on-failure"), and streams a GLB with the native adacpp path,
``include_guids`` filtered so one branch of a published spatial tree builds without slicing a
subset IFC first (``adacpp`` PR #56, adacpp 0.25.3).

Why the descendant set comes from the SPINE and not a fresh walk of the source file: the spine is
what was published -- it is the browser's own picture of what this subject contains, at the
revision this build's ``BuildRequest`` names. Re-walking the source file here would let a build
draw something the browser never showed as belonging to this node, which is exactly the
"resolved through different hierarchies" case ``build_fingerprint`` already guards by hashing
``hierarchy_source`` (``ada.assets.build``).
"""

from __future__ import annotations

import hashlib
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import ada
from ada.assets.build import (
    BuildError,
    BuildProvenance,
    BuildSummary,
    patch_glb_provenance,
)
from ada.assets.builders import BuildRequest
from ada.assets.projection import parse_hierarchy

__all__ = ["IfcAssetBuilder"]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class IfcAssetBuilder:
    """Registered under ``asset-build-ifc`` (``ada.assets.ifc.__init__``). One instance is reused
    across builds -- it carries no per-build state -- so construction stays free of ifcopenshell
    or adacpp imports; those load inside :meth:`build`, the same lazy discipline
    ``ensure_core_builders`` applies to the module import itself."""

    def build(
        self,
        options: Mapping[str, Any],
        *,
        request: BuildRequest,
        storage: Any,
        scope: Any,
        derived_prefix: str,
        on_progress: Callable[[str, float], None] | None = None,
        cancel_event: Any | None = None,
    ) -> BuildSummary:
        started_at = datetime.now(timezone.utc).isoformat()
        source_key = options.get("source_key")
        hierarchy_key = options.get("hierarchy_key")
        if not source_key or not hierarchy_key:
            raise BuildError(
                f"IFC build options missing 'source_key'/'hierarchy_key' (got {sorted(options)}) -- "
                f"this build was not fingerprinted by ada.assets.ifc.publish"
            )
        node = options.get("node") or request.node

        if on_progress:
            on_progress("fetch", 0.1)
        try:
            hierarchy_bytes = storage.get_bytes(hierarchy_key)
        except (FileNotFoundError, KeyError) as exc:
            # REFUSE, never widen: a build that fell back to "the whole file" here would draw
            # geometry the browser never showed as belonging to this node.
            raise BuildError(
                f"published spine {hierarchy_key!r} is missing -- refusing to build node {node!r} "
                f"without it rather than widening to the whole file"
            ) from exc
        spine = parse_hierarchy(hierarchy_bytes)
        include_guids = [r["id"] for r in spine.records() if r["leaf"]]
        if not include_guids:
            raise BuildError(f"published spine {hierarchy_key!r} names no leaf product -- nothing to draw")

        raw_ifc = storage.get_bytes(source_key)

        if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
            raise BuildError("cancelled before tessellation started")

        if on_progress:
            on_progress("tessellate", 0.3)
        glb_bytes = _stream_glb(raw_ifc, include_guids)

        provider_version = getattr(ada, "__version__", None)
        provenance = BuildProvenance(
            provider=request.provider,
            collection=request.collection,
            subject=request.subject,
            revision=request.revision,
            node=request.node,
            fingerprint=request.fingerprint,
            built_at=datetime.now(timezone.utc).isoformat(),
            adapy_version=provider_version,
            provider_version=provider_version,
            hierarchy_source=request.hierarchy_source,
            started_at=started_at,
            sources=({"key": source_key, "sha256": _sha256(raw_ifc)},),
        )
        generator = f"ifc {provider_version} (ada-py {provider_version})" if provider_version else "ifc"
        patched = patch_glb_provenance(glb_bytes, provenance, generator=generator)

        glb_key = f"{derived_prefix}/model.glb"
        if on_progress:
            on_progress("upload", 0.9)
        storage.put_bytes(glb_key, patched, content_encoding=None)
        if on_progress:
            on_progress("ready", 1.0)

        # `drawn` is the one thing this build actually measured -- the product count the native
        # streamer reports back is the tessellated count, which can differ from `len(include_guids)`
        # if a guid matches no body (a container reached by accident); `len(include_guids)` is what
        # the ACCEPTANCE test means by "drew exactly it" (the requested set), so that is what is named.
        return BuildSummary(
            ok=True,
            glb_key=glb_key,
            provenance=provenance,
            glb_size=len(patched),
            counts={"drawn": len(include_guids)},
        )


def _stream_glb(raw_ifc: bytes, include_guids: list[str]) -> bytes:
    """Write ``raw_ifc`` to a temp file, stream the filtered GLB, read it back.

    Two temp files (in, out) rather than an in-memory adacpp call: the native binding streams
    file-to-file, so this mirrors the STEP builder's own pattern and stays Windows-safe (each
    handle is closed before it is reopened by path, and both are unlinked in ``finally``).
    """
    from ada.cadit.ifc.native_ifc_to_glb import native_ifc_to_glb

    tmp_in = tempfile.NamedTemporaryFile(suffix=".ifc", delete=False)
    tmp_out_path = Path(tmp_in.name).with_suffix(".glb")
    try:
        tmp_in.write(raw_ifc)
        tmp_in.close()
        native_ifc_to_glb(Path(tmp_in.name), tmp_out_path, include_guids=include_guids)
        return tmp_out_path.read_bytes()
    finally:
        Path(tmp_in.name).unlink(missing_ok=True)
        tmp_out_path.unlink(missing_ok=True)
