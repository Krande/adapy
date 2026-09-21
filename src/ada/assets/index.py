"""Fold a flat object listing into ``collection -> subject -> revision -> files``.

This lives on the SERVER on purpose. The browser must never list a whole scope to answer "which
revisions exist" -- a scope holds tens of thousands of derived blobs, and the answer it wants is a
few hundred bytes. One ``list_prefix("assets/<collection>/")`` in, one folded index out.

Keys that do not parse are not dropped silently. They go to ``malformed`` with the reason, because
the usual cause is a publisher writing a key the grammar refuses, and a publisher that cannot see
its own bad keys will keep writing them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

from ada.assets.keys import (
    ASSET_PREFIX,
    STAGING_SEGMENT,
    AssetKeyError,
    parse_asset_key,
)
from ada.assets.manifest import MANIFEST_FILENAME

__all__ = ["AssetIndex", "MalformedKey", "RevisionEntry", "SubjectEntry", "fold_listing"]


@dataclass(frozen=True)
class MalformedKey:
    key: str
    reason: str


@dataclass(frozen=True)
class RevisionEntry:
    revision: str
    files: tuple[str, ...]

    @property
    def has_manifest(self) -> bool:
        """A revision without ``asset.json`` is a half-written publish.

        Manifests are written last, so this is the signal that a publish died partway rather than
        that something was deleted.
        """
        return MANIFEST_FILENAME in self.files


@dataclass(frozen=True)
class SubjectEntry:
    subject: str
    revisions: tuple[RevisionEntry, ...]  # newest first

    @property
    def latest(self) -> RevisionEntry | None:
        return self.revisions[0] if self.revisions else None

    @property
    def latest_complete(self) -> RevisionEntry | None:
        """Newest revision that actually has a manifest -- what a reader should resolve to."""
        return next((r for r in self.revisions if r.has_manifest), None)


@dataclass(frozen=True)
class AssetIndex:
    collections: Mapping[str, tuple[SubjectEntry, ...]] = field(default_factory=dict)
    malformed: tuple[MalformedKey, ...] = ()

    def subjects(self, collection: str) -> tuple[SubjectEntry, ...]:
        return self.collections.get(collection, ())

    def subject(self, collection: str, subject: str) -> SubjectEntry | None:
        return next((s for s in self.subjects(collection) if s.subject == subject), None)

    def to_dict(self) -> dict:
        return {
            "collections": {
                c: [
                    {
                        "subject": s.subject,
                        "revisions": [{"revision": r.revision, "files": list(r.files)} for r in s.revisions],
                    }
                    for s in subs
                ]
                for c, subs in self.collections.items()
            },
            "malformed": [{"key": m.key, "reason": m.reason} for m in self.malformed],
        }


def fold_listing(keys: Iterable[str]) -> AssetIndex:
    """Fold raw object keys into the index.

    Revisions come back NEWEST FIRST, which is a plain reverse sort -- the compact-UTC revision
    form makes lexical order chronological order, so no parsing is needed here.
    """
    tree: dict[str, dict[str, dict[str, list[str]]]] = {}
    malformed: list[MalformedKey] = []

    for key in keys:
        # Staged uploads live under the same prefix by design and are not assets yet. Skipping them
        # explicitly keeps them out of `malformed`, which is meant to surface publisher bugs.
        if key.startswith(f"{ASSET_PREFIX}/{STAGING_SEGMENT}/"):
            continue
        if not key.startswith(f"{ASSET_PREFIX}/"):
            continue  # not ours; a scope holds plenty else
        if key.endswith("/"):
            continue  # directory marker
        try:
            parsed = parse_asset_key(key)
        except AssetKeyError as exc:
            malformed.append(MalformedKey(key=key, reason=str(exc)))
            continue
        tree.setdefault(parsed.collection, {}).setdefault(parsed.subject, {}).setdefault(parsed.revision, []).append(
            parsed.filename
        )

    collections = {
        collection: tuple(
            SubjectEntry(
                subject=subject,
                revisions=tuple(
                    RevisionEntry(revision=rev, files=tuple(sorted(files)))
                    for rev, files in sorted(revs.items(), reverse=True)
                ),
            )
            for subject, revs in sorted(subjects.items())
        )
        for collection, subjects in sorted(tree.items())
    }
    return AssetIndex(collections=collections, malformed=tuple(malformed))
