"""Read a check's members from ANOTHER provider's publish of the same elements -- matched by name.

WHY. Several providers can publish into one collection, each with its own node ids for the same
physical element: one derives opaque ids from the element's full name, another uses its own refs.
Only some of them can READ a node into objects (``ada.assets.concepts``); a provider that publishes
meshes only cannot, and a check over its nodes fails with "cannot be read into objects in this
process". The element's NAME is what the publishes have in common, so a member picked in one
provider's tree can be checked with the geometry another provider reads -- which is what
``ClashOptions.geometry_provider`` asks for.

THE MATCH, in order:

1. The name to look for is the member's ``element``; for a whole node it is that node's LABEL, read
   from the spine its own provider published (the node's subject, then the collection index).
2. The geometry provider's candidates are the subjects of the SAME collection whose newest complete
   revision it published (the collection-level subject, which every provider publishing into the
   collection shares, is searched for that provider's newest publish). Their spines are indexed by
   normalised label AND by the row's ``full_name`` where the spine publishes one (a provider may
   label a row with an id of its own while another names the same element by its full name):
   trimmed, a leading ``/`` optional; compared case-sensitively first and case-insensitively only
   when that finds nothing.
3. One node id must answer. Where several do, those no readable subject covers are dropped first:
   they cannot be read whichever is meant, and they are what a provider's older publish leaves
   behind when its ids for the same element have since changed. Two readable nodes under one name
   are ambiguous and the member is left out with a warning naming both -- guessing would check the
   wrong steel.
4. The node is read through a subject that can be READ (its manifest claims a build): the node's own
   subject when it is one, as a whole; otherwise the narrowest readable subject whose spine holds
   it, with the element found in that read by NAME alone (an empty ``path``) -- the row's full name
   where published, else its label. The spine's intermediate labels are the provider's display
   names, not promised to be the names its objects carry, so a path built from them could refuse a
   read that the name alone resolves.
5. A spine may stop short of the member's depth. When no row answers to the member's name, its
   ancestors (from its OWN provider's spine, nearest first) are looked up instead. The first that
   answers is read as in 4 when the provider's spine publishes nothing below it, and the member is
   found in that read by its own name; a member the read does not hold is then reported by the
   reader, like any member it cannot find. When the spine does go on below that ancestor, the
   provider has no such member, and it is left out.

A member that cannot be matched is a WARNING and is left out, the same rule ``group_model`` keeps
for a member it cannot find: only a check where nothing resolves fails. File members are left as
they are (a file has one reader and no provider), and so is a node the geometry provider published.

Every read is cached for the job, so a group of hundreds of members costs one listing per
collection and one manifest and one spine per subject. A storage that also offers
``get_many(keys) -> {key: bytes | None}`` has each provider's manifests and spines fetched in one
batch -- what the API's async storage does (``routes/clash_check.py``), where a read per round trip
would make the plan as slow as the slowest of a few thousand.

ONE IMPLEMENTATION, TWO CALLERS. The worker re-addresses a group before it reads anything
(``group_model``); the API asks the same question BEFORE a check, to tell the user which members
the geometry provider has no node for (:func:`plan_geometry`). Both run :func:`remap_group`, so the
plan cannot promise a match the check then misses. Only stdlib and ``ada.assets`` are imported, all
of it JSON, so the slim API can import this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

__all__ = [
    "MATCH_RULES",
    "NO_PROVIDER_GEOMETRY",
    "GeometryRemap",
    "GeometrySourceIndex",
    "node_group",
    "normalise_label",
    "plan_geometry",
    "remap_group",
    "remap_to_provider",
]


#: The version of the match rules above. A result read through another provider's geometry is
#: cached under it (``routes/clash_check._geometry_options_token``): change it whenever a change
#: here can match a member differently, so results made under the old rules are not served.
MATCH_RULES = 3

#: The cause of a member left out because the provider published NOTHING in its collection: no
#: name can match there, and only asking the provider for the geometry changes that. Also the
#: error code a group whose every member was left out for it fails with (``group_model``).
NO_PROVIDER_GEOMETRY = "no_provider_geometry"


def normalise_label(label: Any) -> str:
    """Trimmed, with one leading ``/`` dropped -- a full name and its bare form are one name."""
    text = str(label or "").strip()
    return text[1:] if text.startswith("/") else text


@dataclass(frozen=True)
class _Spine:
    subject: str
    revision: str
    manifest: Any
    #: ``(id, label)`` per row, in published order.
    rows: tuple[tuple[str, str], ...]

    @property
    def readable(self) -> bool:
        # `part_for_manifest` refuses a subject with no build options: there is no source to read.
        return getattr(self.manifest, "build", None) is not None

    @property
    def node(self) -> str:
        return str(getattr(self.manifest, "node", None) or self.subject)


@dataclass
class _ProviderCatalog:
    spines: list[_Spine]
    #: normalised label or full name -> {row id: label as published}
    exact: dict[str, dict[str, str]]
    folded: dict[str, dict[str, str]]
    #: row id -> the name its object carries when read: the full name where one is published,
    #: else the label (a label may be a bare id where the full name is what a reader names by).
    names: dict[str, str] = field(default_factory=dict)
    #: Row ids some published row names as its parent: where the spine goes deeper.
    branches: frozenset[str] = frozenset()

    def lookup(self, name: str) -> dict[str, str]:
        """The rows named ``name``; of several, only those a readable subject covers, unless none is."""
        norm = normalise_label(name)
        hits = self.exact.get(norm) or self.folded.get(norm.casefold()) or {}
        if len(hits) <= 1:
            return hits
        return {i: lab for i, lab in hits.items() if self.covers(i)} or hits

    def covers(self, node_id: str) -> list[_Spine]:
        """The readable subjects holding ``node_id``: the row itself, or a subject whose spine lists it."""
        return [s for s in self.spines if s.readable and (s.node == node_id or any(i == node_id for i, _ in s.rows))]


class GeometrySourceIndex:
    """The published manifests and spines of a scope, read once per job.

    ``storage`` is the synchronous facade a worker hands a provider -- ``list_keys`` and
    ``get_bytes`` are all this reads, so the killable child's proxy serves it unchanged. An optional
    ``get_many`` is used to fetch a provider's manifests and spines in one batch (module docstring).
    """

    def __init__(self, storage: Any) -> None:
        self._storage = storage
        self._listings: dict[str, Any] = {}
        self._manifests: dict[tuple[str, str, str], Any] = {}
        #: ``(id, label, provider)`` per row; provider is the row's own, else the slice's.
        self._spines: dict[tuple[str, str, str], tuple[tuple[str, str, str], ...] | None] = {}
        #: Row id -> parent row id, per spine (filled beside ``_spines``).
        self._parents: dict[tuple[str, str, str], dict[str, str | None]] = {}
        #: Row id -> full name, per spine, for the rows that publish one.
        self._full_names: dict[tuple[str, str, str], dict[str, str]] = {}
        self._catalogs: dict[tuple[str, str], _ProviderCatalog] = {}
        #: Blobs fetched ahead by ``get_many``, each consumed by the one parse that wanted it
        #: (None: the batch found nothing there).
        self._fetched: dict[str, bytes | None] = {}

    # -- reads ------------------------------------------------------------------------------

    def _prefetch(self, keys: Iterable[str]) -> None:
        get_many = getattr(self._storage, "get_many", None)
        if get_many is None:
            return
        wanted = sorted({k for k in keys if k not in self._fetched})
        if wanted:
            self._fetched.update(get_many(wanted))

    def _get(self, key: str) -> bytes:
        if key in self._fetched:
            raw = self._fetched.pop(key)
            if raw is None:
                raise FileNotFoundError(key)
            return raw
        return self._storage.get_bytes(key)

    def listing(self, collection: str):
        from ada.assets.index import fold_listing
        from ada.assets.keys import ASSET_PREFIX

        if collection not in self._listings:
            self._listings[collection] = fold_listing(self._storage.list_keys(f"{ASSET_PREFIX}/{collection}/"))
        return self._listings[collection]

    def manifest(self, collection: str, subject: str, revision: str):
        """The manifest at exactly this revision, or None when there is none (or it is unreadable)."""
        from ada.assets.keys import asset_key
        from ada.assets.manifest import MANIFEST_FILENAME, ManifestError, parse_manifest

        key = (collection, subject, revision)
        if key not in self._manifests:
            manifest = None
            try:
                raw = self._get(asset_key(collection, subject, revision, MANIFEST_FILENAME))
                manifest = parse_manifest(_ungzip(raw))
            except (FileNotFoundError, KeyError, ValueError, ManifestError):
                manifest = None
            self._manifests[key] = manifest
        return self._manifests[key]

    def latest_manifest(self, collection: str, subject: str, *, provider: str | None = None):
        """The newest complete revision's manifest -- by ``provider`` when one is named."""
        entry = self.listing(collection).subject(collection, subject)
        if entry is None:
            return None
        for rev in entry.revisions:
            if not rev.has_manifest:
                continue
            manifest = self.manifest(collection, subject, rev.revision)
            if manifest is None:
                continue
            if provider is None or manifest.provider == provider:
                return manifest
            if subject != collection:
                # Node ids are a provider's own, so a node subject's newest publish says whose it
                # is; only the collection-level subject is shared and worth searching further back.
                return None
        return None

    @staticmethod
    def _spine_key(manifest) -> str:
        from ada.assets.keys import asset_key
        from ada.assets.manifest import HIERARCHY_FILENAME

        entry = next((a for a in manifest.artefacts if a.role == "hierarchy"), None)
        if entry is not None:
            return entry.key or asset_key(manifest.collection, manifest.subject, manifest.revision, entry.file)
        return asset_key(manifest.collection, manifest.subject, manifest.revision, HIERARCHY_FILENAME)

    def spine(self, manifest) -> tuple[tuple[str, str, str], ...] | None:
        """``(id, label, provider)`` rows of the hierarchy published beside ``manifest``, or None."""
        from ada.assets.projection import HierarchyError, parse_hierarchy

        key = (manifest.collection, manifest.subject, manifest.revision)
        if key not in self._spines:
            rows = None
            parents: dict[str, str | None] = {}
            full_names: dict[str, str] = {}
            try:
                sliced = parse_hierarchy(self._get(self._spine_key(manifest)))
                records = [r for r in sliced.records() if r.get("id") is not None]
                rows = tuple((str(r["id"]), str(r.get("label") or ""), str(r.get("provider") or "")) for r in records)
                parents = {str(r["id"]): (str(r["parent"]) if r.get("parent") is not None else None) for r in records}
                full_names = {str(r["id"]): str(r["full_name"]) for r in records if r.get("full_name")}
            except (FileNotFoundError, KeyError, ValueError, HierarchyError):
                rows = None
            self._spines[key] = rows
            self._parents[key] = parents
            self._full_names[key] = full_names
        return self._spines[key]

    def ancestor_names(self, member: Mapping[str, Any]) -> list[str]:
        """The names of the member's ancestors in its OWN provider's spine, nearest first -- each
        row's full name where it publishes one, else its label. Empty when no spine places it."""
        target = member["target"]
        if target.get("kind") != "node":
            return []
        row = self.member_row(member)
        if row is None:
            return []
        own = self._target_manifest(target)
        searched = [own] if own is not None else []
        index = self.latest_manifest(
            str(target["collection"]), str(target["collection"]), provider=own.provider if own is not None else None
        )
        if index is not None:
            searched.append(index)
        for manifest in searched:
            rows = self.spine(manifest) or ()
            key = (manifest.collection, manifest.subject, manifest.revision)
            parents = self._parents.get(key, {})
            if row not in parents:
                continue
            labels = {i: lab for i, lab, _prov in rows}
            full = self._full_names.get(key, {})
            out: list[str] = []
            cur = parents.get(row)
            while cur is not None and len(out) < 256:
                name = full.get(cur) or labels.get(cur, "")
                if name:
                    out.append(name)
                cur = parents.get(cur)
            return out
        return []

    def member_row(self, member: Mapping[str, Any]) -> str | None:
        """The row id the member's OWN provider published it under -- what that provider (or one
        that finds nodes by another's rows) is asked for. A whole node is its own id; an element is
        the row of its subject's spine named like it, the one whose ancestors end with ``path`` when
        the name is not unique. None when the spine does not place it."""
        target = member["target"]
        if target.get("kind") != "node":
            return None
        element = member.get("element")
        if not element:
            return str(target.get("node") or target["subject"])
        manifest = self._target_manifest(target)
        if manifest is None:
            return None
        rows = self.spine(manifest) or ()
        parents = self._parents.get((manifest.collection, manifest.subject, manifest.revision), {})
        want = normalise_label(str(element))
        hits = [i for i, lab, _prov in rows if normalise_label(lab) == want]
        if not hits:
            folded = want.casefold()
            hits = [i for i, lab, _prov in rows if normalise_label(lab).casefold() == folded]
        if len(hits) <= 1:
            return hits[0] if hits else None
        labels = {i: lab for i, lab, _prov in rows}
        path = [normalise_label(str(p)) for p in (member.get("path") or ())]

        def chain(node_id: str) -> list[str]:
            out: list[str] = []
            cur: str | None = node_id
            while cur is not None and len(out) < 256:
                out.append(normalise_label(labels.get(cur, "")))
                cur = parents.get(cur)
            return out[::-1]

        if path:
            placed = [i for i in hits if chain(i)[-len(path) :] == path]
            if len(placed) == 1:
                return placed[0]
        return None

    # -- the geometry provider's side --------------------------------------------------------

    def catalog(self, collection: str, provider: str) -> _ProviderCatalog:
        from ada.assets.keys import asset_key
        from ada.assets.manifest import MANIFEST_FILENAME

        key = (collection, provider)
        if key in self._catalogs:
            return self._catalogs[key]
        subjects = self.listing(collection).subjects(collection)
        # Every subject's newest complete manifest in one batch: that is the one `latest_manifest`
        # reads first, and for every subject but the collection's own the only one.
        self._prefetch(
            asset_key(collection, entry.subject, rev.revision, MANIFEST_FILENAME)
            for entry in subjects
            if (rev := entry.latest_complete) is not None
            and (collection, entry.subject, rev.revision) not in self._manifests
        )
        manifests = [
            (entry.subject, m)
            for entry in subjects
            if (m := self.latest_manifest(collection, entry.subject, provider=provider)) is not None
        ]
        self._prefetch(
            self._spine_key(m) for _, m in manifests if (m.collection, m.subject, m.revision) not in self._spines
        )
        spines: list[_Spine] = []
        for subject, manifest in manifests:
            # Only the rows this provider published: a mixed spine (a collection index listing every
            # provider's branches) also names the OTHER provider's node under the same label, and
            # counting that would make every match ambiguous.
            rows = tuple((i, lab) for i, lab, prov in (self.spine(manifest) or ()) if prov in ("", provider))
            spines.append(_Spine(subject=subject, revision=manifest.revision, manifest=manifest, rows=rows))
        labels: dict[str, str] = {}
        full_names: dict[str, str] = {}
        branches: set[str] = set()
        for spine in spines:
            for node_id, label in spine.rows:
                labels.setdefault(node_id, label)
            m = spine.manifest
            key_m = (m.collection, m.subject, m.revision)
            for node_id, full in self._full_names.get(key_m, {}).items():
                full_names.setdefault(node_id, full)
            ids = {i for i, _ in spine.rows}
            parents = self._parents.get(key_m, {})
            branches.update(p for i, p in parents.items() if p is not None and i in ids)
        exact: dict[str, dict[str, str]] = {}
        folded: dict[str, dict[str, str]] = {}
        for node_id, label in labels.items():
            # A row is found by its label and by its full name: where a provider labels a row with
            # an id of its own, the full name is the one another provider's member is named by.
            for name in {normalise_label(label), normalise_label(full_names.get(node_id, ""))}:
                if not name:
                    continue
                exact.setdefault(name, {})[node_id] = label
                folded.setdefault(name.casefold(), {})[node_id] = label
        names = {node_id: full_names.get(node_id) or label for node_id, label in labels.items()}
        self._catalogs[key] = _ProviderCatalog(
            spines=spines, exact=exact, folded=folded, names=names, branches=frozenset(branches)
        )
        return self._catalogs[key]

    # -- the member's own side ---------------------------------------------------------------

    def provider_of(self, target: Mapping[str, Any]) -> str | None:
        if target.get("provider"):
            return str(target["provider"])
        manifest = self._target_manifest(target)
        return manifest.provider if manifest is not None else None

    def _target_manifest(self, target: Mapping[str, Any]):
        collection, subject = str(target["collection"]), str(target["subject"])
        if target.get("revision"):
            return self.manifest(collection, subject, str(target["revision"]))
        return self.latest_manifest(collection, subject)

    def node_label(self, target: Mapping[str, Any]) -> str | None:
        """The label the member's OWN provider published for its node: its subject's spine first,
        then the collection index (a node is usually listed by its parent, not by itself)."""
        collection = str(target["collection"])
        node = str(target.get("node") or target["subject"])
        own = self._target_manifest(target)
        searched = [own] if own is not None else []
        index = self.latest_manifest(collection, collection, provider=own.provider if own is not None else None)
        if index is not None:
            searched.append(index)
        for manifest in searched:
            label = next((lab for i, lab, _prov in (self.spine(manifest) or ()) if i == node), None)
            if label:
                return label
        return None


def _ungzip(raw: bytes) -> bytes:
    import gzip

    if isinstance(raw, (bytes, bytearray)) and bytes(raw[:2]) == b"\x1f\x8b":
        return gzip.decompress(bytes(raw))
    return raw


def _describe(member: Mapping[str, Any]) -> str:
    target = member["target"]
    if target.get("kind") == "file":
        where = f"file {target['source_key']!r}"
    else:
        where = f"node {target['collection']}/{target['subject']} ({target.get('node') or target['subject']})"
    return f"{member['element']!r} in {where}" if member.get("element") else where


def remap_to_provider(
    member: Mapping[str, Any],
    provider: str,
    *,
    index: GeometrySourceIndex,
) -> tuple[dict | None, str | None]:
    """``member`` re-addressed to ``provider``'s publish of the same element.

    Returns ``(member, None)`` when nothing needs to change (a file, or a node ``provider`` already
    published), ``(remapped, None)`` on a match, and ``(None, warning)`` when the member cannot be
    read from ``provider`` -- the caller leaves it out and reports the warning.
    """
    target = member["target"]
    if target.get("kind") != "node":
        return dict(member), None
    own = index.provider_of(target)
    if own == provider:
        return dict(member), None

    collection = str(target["collection"])
    name = member.get("element") or index.node_label(target)
    if not name:
        return None, (
            f"{_describe(member)}: its provider published no label for node "
            f"{target.get('node') or target['subject']!r}, so it cannot be matched to {provider!r} by name; left out"
        )

    catalog = index.catalog(collection, provider)
    if not catalog.spines:
        return (
            None,
            f"{_describe(member)}: provider {provider!r} published nothing in collection {collection!r}; left out",
        )
    hits = catalog.lookup(name)
    if len(hits) > 1:
        return None, _ambiguous(member, provider, name, hits)
    if hits:
        ((node_id, label),) = hits.items()
        own_subject = next((s for s in catalog.spines if s.node == node_id and s.readable), None)
        if own_subject is not None:
            # The element is a subject of its own: read it whole, nothing to find inside it.
            return {"target": _node_target(provider, collection, own_subject), "element": None, "path": []}, None
        # Found in the read by the name its object carries, which is not always the row's label.
        return _found_in_cover(
            member, provider, collection, catalog, node_id, label, catalog.names.get(node_id) or label
        )

    # Not a row of the provider's spine. Where that spine STOPS at an ancestor (publishes nothing
    # below it), the member may be inside that ancestor's read: read it, and find the member there
    # by its own name. Where the spine goes on below the ancestor without the member, the provider
    # does not have it.
    for ancestor in index.ancestor_names(member):
        found = catalog.lookup(ancestor)
        if len(found) > 1:
            return None, _ambiguous(member, provider, ancestor, found)
        if not found:
            continue
        ((node_id, label),) = found.items()
        if node_id in catalog.branches:
            break
        return _found_in_cover(member, provider, collection, catalog, node_id, label, name)
    return (
        None,
        f"{_describe(member)}: no node of provider {provider!r} in {collection!r} is named {name!r}; left out",
    )


class _MissingGeometry(str):
    """A warning that is a missing-geometry one: the provider LISTS the element (its tree names it)
    but published no subject that reads into objects there -- a tree-only export. The remedy is the
    same as for an empty collection (ask the provider for the geometry), so the caller tags it."""


def _node_target(provider: str, collection: str, spine: _Spine) -> dict:
    return {
        "kind": "node",
        "provider": provider,
        "collection": collection,
        "subject": spine.subject,
        "revision": spine.revision,
        "node": spine.node,
    }


def _ambiguous(member: Mapping[str, Any], provider: str, name: str, hits: Mapping[str, str]) -> str:
    ids = ", ".join(sorted(hits))
    return (
        f"{_describe(member)}: {len(hits)} nodes of provider {provider!r} are named {name!r} ({ids}), "
        "so which one is meant cannot be told from the name; left out"
    )


def _found_in_cover(
    member: Mapping[str, Any],
    provider: str,
    collection: str,
    catalog: _ProviderCatalog,
    node_id: str,
    label: str,
    element: str,
) -> tuple[dict | None, str | None]:
    """``element`` read from the narrowest readable subject of ``provider`` that holds ``node_id``:
    the row itself when it is a subject, else a subject whose spine lists it."""
    covers = catalog.covers(node_id)
    if not covers:
        return None, _MissingGeometry(
            f"{_describe(member)}: provider {provider!r} lists {label!r} ({node_id}) but publishes no "
            "subject covering it that can be read into objects (only its tree is published here); left out"
        )
    # The narrowest cover: the smallest read, and the fewest other objects to share the name with.
    cover = min(covers, key=lambda s: (len(s.rows), s.subject))
    return {"target": _node_target(provider, collection, cover), "element": element, "path": []}, None


@dataclass
class GeometryRemap:
    """A group re-addressed to one geometry provider."""

    #: The EFFECTIVE group, normalised -- what the model is built from and what a detail job
    #: rebuilds from without searching again.
    group: dict
    #: ``{"from": member, "to": member}`` for every member that was re-addressed.
    remapped: list[dict] = field(default_factory=list)
    #: The members left out, as asked for.
    unmatched: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: The members read as they are: files, and nodes ``provider`` published itself.
    unchanged: list[dict] = field(default_factory=list)
    #: Per unmatched member, in the same order: ``{"member", "reason", "label", "row", "cause"}`` --
    #: the name it was looked for under (None when its own provider published none), which is what a
    #: provider asked to publish the element has to find it by, the row id its own provider published
    #: it under (None when its spine does not place it), and :data:`NO_PROVIDER_GEOMETRY` when the
    #: provider published nothing at all in the member's collection (None for any other reason).
    unmatched_detail: list[dict] = field(default_factory=list)

    @property
    def empty_collections(self) -> list[str]:
        """The collections, in member order, where the provider published nothing for an
        unmatched member: no name could match there, so only a request for the geometry helps."""
        return list(
            dict.fromkeys(
                str(d["member"]["target"]["collection"])
                for d in self.unmatched_detail
                if d.get("cause") == NO_PROVIDER_GEOMETRY
            )
        )

    @property
    def only_missing_geometry(self) -> bool:
        """Every unmatched member was left out because the provider published nothing in its
        collection -- and there was at least one."""
        return bool(self.unmatched_detail) and all(
            d.get("cause") == NO_PROVIDER_GEOMETRY for d in self.unmatched_detail
        )


def remap_group(group: Mapping[str, Any], provider: str, *, storage: Any) -> GeometryRemap:
    """Every member of a normalised group re-addressed to ``provider`` (see the module docstring).

    The result's ``group`` may have FEWER members than the input: unmatched ones are left out, and
    two members that name one element of ``provider`` become one. It may also be empty, which the
    caller reports the way it reports a group where nothing resolved.
    """
    from ada.clash.group import normalise_group

    index = GeometrySourceIndex(storage)
    members: list[dict] = []
    remapped: list[dict] = []
    unmatched: list[dict] = []
    unmatched_detail: list[dict] = []
    unchanged: list[dict] = []
    warnings: list[str] = []
    files = 0
    for member in group.get("members") or ():
        new, warning = remap_to_provider(member, provider, index=index)
        if new is None:
            reason = warning or f"{_describe(member)}: left out"
            unmatched.append(dict(member))
            warnings.append(reason)
            target = member["target"]
            label = member.get("element") or (index.node_label(target) if target.get("kind") == "node" else None)
            # The row its own provider published it under, so a request for it needs no tree walk.
            row = index.member_row(member) if target.get("kind") == "node" else None
            # Nothing of the provider in the collection at all (a cached catalog, no further read).
            # ... or only a TREE of it: subjects, but none that reads into objects (a tree-only export
            # lists every element and holds the geometry of none), or a listed element none covers.
            empty = target.get("kind") == "node" and (
                isinstance(warning, _MissingGeometry)
                or not any(s.readable for s in index.catalog(str(target["collection"]), provider).spines)
            )
            unmatched_detail.append(
                {
                    "member": dict(member),
                    "reason": reason,
                    "label": label or None,
                    "row": row,
                    "cause": NO_PROVIDER_GEOMETRY if empty else None,
                }
            )
            continue
        if member["target"].get("kind") == "file":
            files += 1
            unchanged.append(dict(member))
        elif new != dict(member):
            remapped.append({"from": dict(member), "to": new})
        else:
            unchanged.append(dict(member))
        members.append(new)
    if files:
        warnings.append(
            f"{files} file member(s) were read from the file itself: a file has no provider, so "
            f"its geometry cannot come from {provider!r}"
        )
    name = str(group.get("name") or "group")
    effective = normalise_group({"name": name, "members": members}) if members else {"name": name, "members": []}
    return GeometryRemap(
        group=effective,
        remapped=remapped,
        unmatched=unmatched,
        warnings=warnings,
        unchanged=unchanged,
        unmatched_detail=unmatched_detail,
    )


def node_group(*, provider: str, collection: str, subject: str, revision: str, node: str | None) -> dict:
    """A published node as a one-member group -- how a single node is checked through another
    provider's geometry (``from_asset``), and how the API plans that check. One builder, so the
    member the plan reports is the member the check reads."""
    from ada.clash.group import normalise_group

    return normalise_group(
        {
            "name": node or subject,
            "members": [
                {
                    "target": {
                        "kind": "node",
                        "provider": provider,
                        "collection": collection,
                        "subject": subject,
                        "revision": revision,
                        "node": node or subject,
                    }
                }
            ],
        }
    )


def plan_geometry(group: Mapping[str, Any], provider: str, *, storage: Any) -> dict:
    """What a check with ``geometry_provider=provider`` would read, without reading any geometry.

    ``{geometry_provider, matched: [{from, to}], unmatched: [{member, reason, label}],
    unchanged: [member]}`` -- the wire body of ``POST .../clash-check/geometry-plan``. The same
    :func:`remap_group` the worker runs, over the same normalised, revision-resolved group, so a
    member reported matched here is matched there.
    """
    remap = remap_group(group, provider, storage=storage)
    return {
        "geometry_provider": provider,
        "matched": list(remap.remapped),
        "unmatched": list(remap.unmatched_detail),
        "unchanged": list(remap.unchanged),
    }
