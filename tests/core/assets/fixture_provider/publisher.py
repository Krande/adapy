"""The ``fixture-lines`` PUBLISHER -- the ``AssetPublisher.derive()`` witness for a private format.

``provider.py::publish_fixture`` already proves the fixture's translation (private newline-JSON in,
core's manifest/hierarchy schemas out) by WRITING straight into a ``FakeStore``. That is the right
shape for the ``AssetTreeProvider``/build tests, but it is not the shape the real publish surface
uses: a real publish is ``derive()`` PLANS, core WRITES (``ada.assets.publish``), so that the owner
gate and the manifests-last ordering are properties of the STORE rather than habits every provider
has to remember (see ``src/ada/assets/publish.py``'s module docstring).

This module is that missing half: :class:`FixtureLinesPublisher` reads the staged private source
back out through the INJECTED ``storage`` (never a file handed to it directly -- a publish's staged
bytes are already in the scope, which is the whole point of staging surviving a reload) and returns
an ORDERED :class:`~ada.assets.publish.PublishPlan` instead of writing anything itself. It derives
EXACTLY the tree ``publish_fixture`` writes (same node set, same delivery assignment, same
collection/hierarchy/manifest shape), so the two are cross-checked by construction rather than by a
separate assertion.

**The semantics-not-format witness (Decision 7).** This publisher sets no ``change`` on any
manifest, gives every artefact list no ``attributes`` role, and never sets ``action`` -- the fixture
provider's whole publish/unpublish/orphan suite has to pass with all three absent, from a provider
whose source format core has no reader for. What core adds on top (``change.published_by`` /
``published_via``) is CORE's own stamp (``ada.assets.publish.stamp_publish``), not something this
provider supplies -- which is exactly the distinction Decision 6 draws.

WHY THIS LIVES IN ``tests/`` AND NOT ``src/ada/``. Registering a publisher for a private format
there would be the layering violation the fixture provider exists to catch. Nothing here runs
unless a test explicitly calls :func:`register_fixture_publisher` -- the same "presence is opt-in,
never an import side effect" discipline ``ada.assets.registry``/``ada.assets.publishers`` already
use for every other registration (see ``builder.py``'s identical note).
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from tests.core.assets.fixture_provider.provider import (
    BUILD_CAPABILITY,
    FIXTURE_PROVIDER_ID,
    FIXTURE_SOURCE_LINES,
    MESH_FILENAME,
    SOURCE_FILENAME,
    _parse_private_source,
    _sha,
    _tiny_glb,
)

from ada.assets.keys import (
    ASSET_PREFIX,
    STAGING_SEGMENT,
    asset_key,
    revision_from_instant,
)
from ada.assets.manifest import (
    HIERARCHY_FILENAME,
    MANIFEST_FILENAME,
    ArtefactEntry,
    AssetManifest,
    BuildSpec,
)
from ada.assets.projection import build_hierarchy
from ada.assets.publish import PlannedWrite, PublishPlan
from ada.assets.publishers import register_asset_publisher

__all__ = [
    "DEFAULT_COLLECTION",
    "DEFAULT_INSTANT",
    "FixtureLinesPublisher",
    "fixture_source_bytes",
    "register_fixture_publisher",
    "stage_fixture_source",
]

# Kept identical to `provider.py::publish_fixture`'s defaults on purpose: any test that publishes
# through BOTH paths (the direct-write oracle and this derive()-based one) can compare their output
# byte for byte without threading its own instant/collection through each call.
DEFAULT_COLLECTION = "fixture-a"
DEFAULT_INSTANT = "2026-09-21T14:30:01Z"


def fixture_source_bytes() -> bytes:
    """The private newline-delimited source, as an upload would deliver it. Never named outside
    ``tests/`` -- see ``provider.py::_parse_private_source``'s own note."""
    return ("\n".join(json.dumps(line) for line in FIXTURE_SOURCE_LINES)).encode("utf-8")


def stage_fixture_source(store, staging_id: str = "up1") -> str:
    """Put the private source where a real upload would -- ``assets/_staging/<id>/<file>`` -- and
    hand back its key.

    Staging is the ONLY way a real publish reaches a provider's ``derive()``: the staged bytes are
    read back out through ``storage.get_bytes``, never handed to the provider directly, because a
    publish that survives a process restart depends on the bytes being in the store already.
    """
    key = f"{ASSET_PREFIX}/{STAGING_SEGMENT}/{staging_id}/{SOURCE_FILENAME}"
    store.put(key, fixture_source_bytes())
    return key


class FixtureLinesPublisher:
    """``derive()`` for the private vendor-lines format -- PLANS, never writes.

    Mirrors ``provider.py::publish_fixture()``'s derivation exactly (same node set, same delivery
    assignment, same collection/hierarchy/manifest shape) but returns the writes as an ORDERED
    ``PublishPlan`` instead of putting them into a store directly. That is what proves the
    provider-plans/core-writes split (``ada.assets.publish``'s module docstring) rather than merely
    asserting it: nothing here calls ``storage.put_bytes`` at all.
    """

    id = FIXTURE_PROVIDER_ID

    def derive(
        self,
        scope: Any,
        staged: Mapping[str, str],
        *,
        storage: Any,
        collection: str | None = None,
        options: Mapping[str, Any] | None = None,
        dry_run: bool = False,
    ) -> PublishPlan:
        del scope  # this private format carries no scope-shaped concept of its own
        del dry_run  # derive() never writes either way -- see the class docstring
        opts = dict(options or {})
        collection = collection or DEFAULT_COLLECTION
        instant = opts.get("instant", DEFAULT_INSTANT)
        mesh_node = opts.get("mesh_node", "pump-a")

        source_key = staged.get(SOURCE_FILENAME)
        if source_key is None:
            # A caller may stage under any role name it likes (`POST /assets/publish`'s `staged`
            # map is caller-chosen); only when there is exactly one staged file is "which one is
            # the source" unambiguous without a role convention this format does not have.
            if len(staged) != 1:
                raise ValueError(
                    f"fixture-lines derive(): expected a staged {SOURCE_FILENAME!r} or exactly one "
                    f"staged file, got {sorted(staged)}"
                )
            source_key = next(iter(staged.values()))
        raw = storage.get_bytes(source_key)
        records = _parse_private_source(raw)

        revision = revision_from_instant(instant)
        published_source_key = asset_key(collection, collection, revision, SOURCE_FILENAME)

        parents = {r["up"] for r in records if r["up"] is not None}
        nodes: list[dict] = []
        for rec in records:
            ref = rec["ref"]
            is_leaf = ref not in parents
            delivery = "mesh" if ref == mesh_node else ("build" if is_leaf else "")
            nodes.append(
                {
                    "id": ref,
                    "parent": rec["up"],
                    "label": rec["title"],  # vendor "title" -> core "label"
                    "kind": rec["cat"],  # vendor "cat" -> core "kind"
                    "leaf": is_leaf,
                    "delivery": delivery,
                }
            )

        writes: list[PlannedWrite] = [PlannedWrite(published_source_key, raw)]

        for node in nodes:
            # `key=`, never `file=`: every node manifest shares the ONE uploaded source rather than
            # copying it -- the "N leaf manifests, one blob" property Decision 3 keeps from the
            # prior art, and exactly the shape ``ada.assets.unpublish``'s refcount check exists for.
            artefacts = [ArtefactEntry(role="source", key=published_source_key, sha256=_sha(raw), size=len(raw))]
            build = None
            if node["delivery"] == "build":
                build = BuildSpec(
                    capability=BUILD_CAPABILITY,
                    options={"ref": node["id"], "source_key": published_source_key},
                    fingerprint_inputs=("source_key", "ref"),
                )
            if node["delivery"] == "mesh":
                glb = _tiny_glb()
                mesh_key = asset_key(collection, node["id"], revision, MESH_FILENAME)
                writes.append(PlannedWrite(mesh_key, glb))
                artefacts.append(ArtefactEntry(role="mesh", file=MESH_FILENAME, sha256=_sha(glb), size=len(glb)))
            manifest = AssetManifest(
                provider=FIXTURE_PROVIDER_ID,
                collection=collection,
                subject=node["id"],
                revision=revision,
                node=node["id"],
                produced_at=instant,
                published_at=instant,
                delivery=node["delivery"] or "none",
                build=build,
                artefacts=tuple(artefacts),
                counts={"nodes": 1},
                # No `change=`: the fixture format carries no authorship of its own to relay, and
                # this is the field the owner gate exists to police (Decision 6) -- leaving it out
                # entirely is the honest answer, not a `ChangeRecord()` full of Nones.
            )
            writes.append(
                PlannedWrite(asset_key(collection, node["id"], revision, MANIFEST_FILENAME), manifest.to_json())
            )

        slice_ = build_hierarchy(
            provider=FIXTURE_PROVIDER_ID, collection=collection, produced_at=instant, nodes=nodes, depth=3
        )
        hierarchy_bytes = slice_.to_json()
        writes.append(PlannedWrite(asset_key(collection, collection, revision, HIERARCHY_FILENAME), hierarchy_bytes))

        leaves_published = sum(1 for n in nodes if n["leaf"])
        collection_manifest = AssetManifest(
            provider=FIXTURE_PROVIDER_ID,
            collection=collection,
            subject=collection,
            revision=revision,
            node=None,
            produced_at=instant,
            published_at=instant,
            delivery="none",
            artefacts=(
                ArtefactEntry(
                    role="hierarchy", file=HIERARCHY_FILENAME, sha256=_sha(hierarchy_bytes), size=len(hierarchy_bytes)
                ),
                ArtefactEntry(role="source", file=SOURCE_FILENAME, sha256=_sha(raw), size=len(raw)),
            ),
            counts={"nodes": len(nodes), "leaves": leaves_published},
        )
        # Collection manifest LAST, across every subject this publish wrote -- the one signal that
        # "everything this publish promised is here" (mirrors `ada.assets.ifc.publish`'s ordering).
        writes.append(
            PlannedWrite(asset_key(collection, collection, revision, MANIFEST_FILENAME), collection_manifest.to_json())
        )

        return PublishPlan(
            collection=collection,
            revision=revision,
            subjects=tuple(n["id"] for n in nodes),
            writes=tuple(writes),
            counts={"nodes": len(nodes), "leaves": leaves_published},
            # No `change=` at the plan level either -- see the per-manifest note above.
        )


def register_fixture_publisher() -> None:
    """Register :class:`FixtureLinesPublisher` for ``FIXTURE_PROVIDER_ID``. Call from a test's
    setup, never at import -- ``ada.assets.publishers`` must not learn about this format unless a
    test opts in (see the module docstring)."""
    register_asset_publisher(FIXTURE_PROVIDER_ID, FixtureLinesPublisher, label="Fixture publisher (lines)")
