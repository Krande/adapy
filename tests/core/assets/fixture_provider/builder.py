"""The ``asset-build-fixture`` builder -- the ``build`` delivery kind's witness in CI.

Every branch node the fixture provider publishes (``provider.py``) claims ``delivery="build"``
with ``build.capability == BUILD_CAPABILITY`` and ``options = {"ref": <node>, "source_key": <key>}``.
This is the worker-side half of that claim: given those options, read the private source back
through the sync storage facade core hands a builder, "build" a real (if trivial) GLB for the named
ref, patch core's provenance block into it, and return a ``BuildSummary`` that restates the request
core composed the derived key from -- exactly, because ``validate_build_summary`` refuses a summary
that disagrees with even one field of it.

WHY THIS LIVES IN ``tests/`` AND NOT ``src/ada/``. Registering it there would be the layering
violation the fixture provider exists to catch: core must not carry a builder for a format it has
no reader for. So nothing here runs unless a test explicitly calls :func:`register_fixture_builder`
-- the same "presence is opt-in, never an import side effect" discipline
``ada.assets.registry``/``ada.assets.builders`` already use for every other registration.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Mapping

from tests.core.assets.fixture_provider.provider import (
    BUILD_CAPABILITY,
    _parse_private_source,
    _sha,
    _tiny_glb,
)

from ada.assets.build import BuildProvenance, BuildSummary, patch_glb_provenance
from ada.assets.builders import BuildRequest, register_asset_builder

__all__ = ["FixtureAssetBuilder", "register_fixture_builder"]

_PROVIDER_VERSION = "fixture-lines-builder/1"


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class FixtureAssetBuilder:
    """Reads the fixture's private newline-delimited source and "builds" one ref's GLB.

    A real builder would draw geometry for the ref; this one has none to draw, so it is honest
    about that -- ``counts`` names only what it actually measured (how many source records it
    read, and that it drew exactly one node), never a geometry count it never computed.
    """

    def build(
        self,
        options: Mapping[str, Any],
        *,
        request: BuildRequest,
        storage: Any,
        scope: Any,
        derived_prefix: str,
        on_progress: Any = None,
        cancel_event: Any = None,
    ) -> BuildSummary:
        started_at = _now_iso()
        if on_progress is not None:
            on_progress("read-source", 0.2)

        source_key = options.get("source_key")
        ref = options.get("ref")
        if not source_key or not ref:
            raise ValueError(f"asset-build-fixture: options missing 'source_key' and/or 'ref': {dict(options)}")

        raw = storage.get_bytes(source_key)
        records = _parse_private_source(raw)
        if not any(r["ref"] == ref for r in records):
            raise ValueError(f"asset-build-fixture: {ref!r} is not a node in {source_key}")

        if on_progress is not None:
            on_progress("build-glb", 0.6)

        provenance = BuildProvenance(
            provider=request.provider,
            collection=request.collection,
            subject=request.subject,
            revision=request.revision,
            node=request.node,
            fingerprint=request.fingerprint,
            built_at=_now_iso(),
            provider_version=_PROVIDER_VERSION,
            hierarchy_source=request.hierarchy_source,
            started_at=started_at,
            sources=({"key": source_key, "sha256": _sha(raw)},),
        )
        glb = patch_glb_provenance(_tiny_glb(), provenance, generator=f"fixture-lines {_PROVIDER_VERSION}")

        glb_key = f"{derived_prefix}/model.glb"
        if on_progress is not None:
            on_progress("upload", 0.9)
        storage.put_bytes(glb_key, glb, content_encoding=None)

        return BuildSummary(
            ok=True,
            glb_key=glb_key,
            provenance=provenance,
            glb_size=len(glb),
            # Only what this builder actually measured: it read the source (one record count) and
            # drew exactly the one ref it was asked for -- never a geometry metric it never computed.
            counts={"source_records": len(records), "drawn": 1},
        )


def register_fixture_builder() -> None:
    """Register :class:`FixtureAssetBuilder` for ``BUILD_CAPABILITY``. Call from a test's setup,
    never at import -- see the module docstring."""
    register_asset_builder(BUILD_CAPABILITY, FixtureAssetBuilder, label="Fixture builder (lines)")
