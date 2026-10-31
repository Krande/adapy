"""Split collections: one provider collection offered as several, one per source copy.

A provider may find one collection in several places -- the same project code on several file
servers, say, where each copy is live and edited on its own. By default core offers it once and the
provider reads the copy it prefers (an admin can pin another per run: ``asset_collection_choices``).
When the copies are really different datasets, an admin SPLITS the collection instead: core then
offers each chosen copy as a collection of its own, with its own published data, cursor, grants and
schedules, and stops offering the original.

The provider declares what each collection would split into, in a spec field it names:

    asset_collection_splits_field="copies"
    copies={"ABC": [{"collection": "ABC-X", "label": "ABC (X)", "location": "...", ...}, ...]}

(a ``union_fields`` key, so every worker's copies are offered). A copy's id must be a collection name
in its own right: every job for it carries it in the entry's ``collection_option``, exactly as a plain
collection's name. A provider that routes on that option (``capability_option``) maps each copy to
the pool that serves it with ``capability_routes`` (``{"ABC-X": "ABC"}``), see ``plugin_jobs``.

Which collections are split is core's setting, :data:`COLLECTION_SPLITS_SETTING`:
``{provider: {collection: [copy id, ...]}}`` -- the copies an admin chose, the original's key in any
case. A split names only copies the provider still declares; a split whose copies are all gone falls
back to offering the original, so a worker that stops advertising them never hides a collection.

Nothing is migrated when a collection is split: what was published under the original stays where it
is (an admin deletes it when it is no longer wanted), and each copy starts with no data of its own.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ada.config import logger

#: Core's setting: ``{provider: {collection: [copy id, ...]}}``. Public, like the other per-provider
#: collection settings, so the viewer lists the same collections the server does; written by admins.
COLLECTION_SPLITS_SETTING = "public.assets.collection_splits"

#: The spec key naming the provider's field of declared copies.
SPLITS_FIELD_KEY = "asset_collection_splits_field"


def declared_copies(spec: Mapping[str, Any]) -> dict[str, list[dict]]:
    """``{collection: [{collection, label, ...}, ...]}`` as ``spec`` declares it; ``{}`` when it does not.
    Entries without a usable ``collection`` are dropped."""
    field = spec.get(SPLITS_FIELD_KEY)
    raw = spec.get(field) if isinstance(field, str) and field else None
    out: dict[str, list[dict]] = {}
    for collection, entries in raw.items() if isinstance(raw, Mapping) else ():
        if not isinstance(collection, str) or not isinstance(entries, list):
            continue
        kept = [dict(e) for e in entries if isinstance(e, Mapping) and isinstance(e.get("collection"), str)]
        kept = [e for e in kept if e["collection"].strip()]
        if kept:
            out[collection] = kept
    return out


def provider_splits(doc: Any, provider: str) -> dict[str, list[str]]:
    """The split map of ``provider`` out of the setting's document: ``{collection (lower): [copy id]}``.
    Anything malformed reads as no split."""
    raw = doc.get(provider) if isinstance(doc, Mapping) else None
    out: dict[str, list[str]] = {}
    for collection, copies in raw.items() if isinstance(raw, Mapping) else ():
        if isinstance(collection, str) and isinstance(copies, list):
            chosen = [str(c).strip() for c in copies if isinstance(c, str) and c.strip()]
            if chosen:
                out[collection.strip().lower()] = chosen
    return out


async def load_splits(pool, provider: str) -> dict[str, list[str]]:
    """``provider``'s split map from :data:`COLLECTION_SPLITS_SETTING`. Unreadable reads as none: the
    collections are then offered unsplit, as before the setting existed."""
    from . import db as db_module

    if pool is None:
        return {}
    try:
        raw = await db_module.get_setting(pool, COLLECTION_SPLITS_SETTING)
        return provider_splits(json.loads(raw) if raw else {}, provider)
    except Exception:  # noqa: BLE001 - see the docstring
        logger.warning(
            "collection splits of %s are unreadable; offering every collection unsplit", provider, exc_info=True
        )
        return {}


def apply_splits(collections: list[str], spec: Mapping[str, Any], splits: Mapping[str, list[str]] | None) -> list[str]:
    """``collections`` with each split one replaced by its chosen copies, in place, in the provider's
    spelling -- only the copies ``spec`` still declares. A split with none of them left keeps the
    original. With no splits, ``collections`` unchanged."""
    if not splits:
        return list(collections)
    declared = {c.lower(): entries for c, entries in declared_copies(spec).items()}
    out: list[str] = []
    for collection in collections:
        chosen = splits.get(collection.lower())
        offered = {e["collection"].lower(): e["collection"] for e in declared.get(collection.lower(), ())}
        copies = [offered[c.lower()] for c in chosen or () if c.lower() in offered]
        out.extend(copies or [collection])
    return list(dict.fromkeys(out))


def copy_names(spec: Mapping[str, Any]) -> dict[str, str]:
    """``{copy id (lower): copy id}`` for every copy ``spec`` declares, split or not: how a collection
    core holds in lower case is spelled when sent back to the provider."""
    return {e["collection"].lower(): e["collection"] for entries in declared_copies(spec).values() for e in entries}
