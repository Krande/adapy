"""Run a clash check over a PUBLISHED ASSET NODE -- the way in for a provider's own format.

The third way a check starts, beside a source file and a member scan, and the one that needs no
reader in core. ``ada.clash`` has always been model-centred: ``identify_joints`` walks
``get_all_physical_objects(Beam/Plate)``, and the detail hand-off wants those same objects back
(``formats/clash_detail.py``). What decides a check's reach is therefore only ever "can something
produce a model here", and until now that meant a file whose EXTENSION core recognises.

A provider that can read its own format into ``ada`` objects (``ada.assets.concepts``) answers that
question for every format core will never read. Nothing else changes: same identify, same classify,
same match, same ``ada.clash/result@1`` document, so a joint found this way is comparable with one
found from an IFC, and the panel cannot tell them apart -- which is the point. What it CAN tell is
where the model came from, because the provenance says so.

WHAT IS ADDRESSED IS A SUBJECT, NOT A FILE. A published node's manifest names its provider and
carries the options that provider needs; the source may be one blob shared by many subjects, so
"the file" was never the right handle. The subject is.
"""

from __future__ import annotations

from typing import Any

from ada.clash.options import ClashOptions

__all__ = ["part_for_asset_node", "clash_check_from_asset_node"]


def _manifest_for(storage: Any, collection: str, subject: str, revision: str | None):
    from ada.assets.published import PublishedAssetProvider, StorageReader

    reader = StorageReader(list_prefix=storage.list_keys, get_bytes=storage.get_bytes)
    manifest = PublishedAssetProvider(reader).manifest(collection, subject, revision=revision)
    if manifest is None:
        raise FileNotFoundError(
            f"no published manifest for collection={collection!r} subject={subject!r}"
            + (f" at revision={revision!r}" if revision else " (and no complete revision to fall back to)")
        )
    return manifest


def _source_key_of(manifest) -> str:
    """The blob the check ran against, for the result document's own record of it.

    The ``source`` artefact rather than a derived key: a clash result is a statement about the
    published source, and a reader of that document has to be able to find the same bytes.
    """
    from ada.assets.keys import asset_key

    entry = next((a for a in manifest.artefacts if a.role == "source"), None)
    if entry is None:
        # Not fatal -- the document's `source_key` is provenance, not an input -- so the subject
        # itself identifies the check rather than leaving the field empty.
        return f"{manifest.collection}/{manifest.subject}@{manifest.revision}"
    return entry.key or asset_key(manifest.collection, manifest.subject, manifest.revision, entry.file)


def part_for_asset_node(
    *,
    collection: str,
    subject: str,
    storage: Any,
    revision: str | None = None,
    node: str | None = None,
    scope: Any = None,
):
    """The published node as an ``ada.Part``, read by whichever provider owns its format."""
    from ada.assets.concepts import part_for_manifest

    manifest = _manifest_for(storage, collection, subject, revision)
    return part_for_manifest(manifest, storage=storage, scope=scope, node=node)


def clash_check_from_asset_node(
    *,
    collection: str,
    subject: str,
    storage: Any,
    revision: str | None = None,
    node: str | None = None,
    scope: Any = None,
    options: ClashOptions | None = None,
) -> dict:
    """Identify, classify, match and group a published node; return the result document.

    A dict for the same reason ``clash_check_from_members`` returns one: every caller of this is
    across a boundary -- a job payload, a cached artefact, an HTTP body -- and the document is what
    those carry.
    """
    from ada.clash.identify import run_clash_check

    manifest = _manifest_for(storage, collection, subject, revision)
    from ada.assets.concepts import part_for_manifest

    part = part_for_manifest(manifest, storage=storage, scope=scope, node=node)
    result = run_clash_check(
        part,
        source_key=_source_key_of(manifest),
        options=options or ClashOptions(),
        # A joint list is only as trustworthy as the read behind it, and "a provider read its own
        # format" is something a reader of the result -- or of a bug report quoting it -- must not
        # have to infer from which route answered.
        provenance={
            "reader": "provider-concepts",
            "provider": manifest.provider,
            "collection": manifest.collection,
            "subject": manifest.subject,
            "revision": manifest.revision,
        },
    )
    return result.to_dict()
