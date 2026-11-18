# Saved groups: the stored document and its source references

Status: implemented (viewer). Schema `ada.viewer/groups@2`; `@1` is still read.

A saved group is a named set of elements and levels, picked across every loaded model in the viewer
(Scene panel, "Saved in this scope"). This page is for code OUTSIDE the viewer that reads the groups
of a scope: what the document holds, and how to get from a member to the CSG source geometry it was
picked from.

## Where it is

One JSON blob per scope, `_groups/groups.json`, read and written through the scope's blob route:

```
GET /api/scopes/{scope}/blobs/_groups%2Fgroups.json
```

It may be gzip-at-rest like other JSON blobs. Everyone in the scope shares it; the viewer applies
each change to the latest stored document (read, change one group, write).

## The document

```json
{
  "schema": "ada.viewer/groups@2",
  "groups": [
    {
      "id": "g000mwl25abc123",
      "name": "Frame connections",
      "created_at": "2026-01-01T10:00:00.000Z",
      "created_by": "someone",
      "updated_at": "2026-01-01T10:05:00.000Z",
      "members": [ ... ]
    }
  ]
}
```

A reader MUST refuse a `schema` it does not know rather than read it partly: the document is shared,
and a writer that dropped fields it did not understand would strip them for everyone. The viewer
reads `@1` and `@2` and writes `@2`.

## A member

```json
{
  "target": {
    "kind": "node",
    "provider": "solids",
    "collection": "plant",
    "subject": "S10",
    "revision": "20260101T000000Z",
    "node": "N20"
  },
  "element": "Beam 2",
  "path": ["Frame A", "Beams", "Beam 2"],
  "source": {
    "kind": "stored",
    "scope": "user:me",
    "provider": "solids",
    "collection": "plant",
    "subject": "S10",
    "revision": "20260101T000000Z",
    "key": "assets/plant/S10/20260101T000000Z/csg.db",
    "format": "database",
    "sha256": "0c71…",
    "element_id": "N22"
  }
}
```

`target`, `element` and `path` are what `@1` had, unchanged: WHICH MODEL the member was picked in,
and the row's name with the names above it down from that model's root row (`element: null` and
`path: []` is the whole model). They are what the viewer finds a member again by, and exactly what
`POST /api/scopes/{scope}/clash-check` takes as a group member (`ada.clash.group`).

- `target.kind: "node"` -- a published asset node: `provider`, `collection`, `subject` (the node that
  owns the manifest), `revision` (the one that was loaded), `node` (the node the model was loaded at,
  when not the subject itself; `null` otherwise).
- `target.kind: "file"` -- a file loaded by its storage key, `source_key`.

`source` is new in `@2`. It says where the member's SOURCE geometry is -- the CSG database, STEP,
IFC or Genie XML file the viewer's GLB was derived from -- so a consumer reads the source's objects,
not triangles. Absent on a member saved before `@2` (or saved with no server to ask); the viewer
offers "Record sources" in the group's Details to fill it in.

### `source.kind: "stored"`

| Field | Meaning |
| --- | --- |
| `scope` | The scope the source is read through (its wire form, e.g. `user:me`, `project:<id>`). A granted provider dataset is read through the scope too. |
| `provider`, `collection`, `subject`, `revision` | The publish the source belongs to, `revision` PINNED to the one the member was saved against. All `null` for a plain stored file. |
| `key` | The source file's storage key in `scope`. For a publish: the artefact with role `source` in `assets/<collection>/<subject>/<revision>/asset.json` (an absolute `key`, or a sibling `file` of that revision). For a plain stored file: the file itself. |
| `format` | `database` (a provider's own solid-model database, `*.db`, e.g. an external CSG source's `csg.db`: read through that provider), `step`, `ifc`, `genie-xml`, `sat` or `other`, from the file name: which reader to use. |
| `sha256` | The source file's hash as its manifest records it; `null` when unknown (a plain file). |
| `element_id` | The member's own id in that source, where the derived tree carries one: the provider's node id in the published tree, which the publishing provider derives from the source's own element id (a CSG database reference, an IFC GlobalId). For a whole node it is the node's id. `null` when not known -- the member is then found by `element` and `path`, matched against the source's hierarchy by name. |

### `source.kind: "none"`

`{"kind": "none", "reason": ..., "detail": "<human text>"}`. There is no source in the store to refer
to:

| `reason` | When |
| --- | --- |
| `local-file` | The model was opened from the user's disk; nothing of it was stored. The member selects while that file is loaded in the same session, and nothing downstream can read it (a server clash check leaves it out and says so). |
| `mesh-only` | The stored model is a GLB: triangles, no CSG behind it. |
| `no-source-artefact` | The publish's manifest names no `source` artefact (a mesh delivery, a tree-only publish). |
| `unreadable` | The manifest could not be read when the member was saved. |

## Reference, not copy

A member refers to its source with the revision pinned; it does not copy geometry.

- A published revision is immutable, and core never removes one by itself: `assets/<collection>/
  <subject>/<revision>/` goes only when a person unpublishes it
  (`DELETE /api/scopes/{scope}/assets/{collection}/{subject}/{revision}`) or deletes its source
  (`DELETE .../assets/sources/{collection}/{revision}`). There is no retention policy on published
  asset blobs (the admin database retention prunes database areas, not these).
- If the pinned revision IS deleted, `source.key` stops resolving, and so does `target.revision` (it
  is the same revision): a server clash check over the group is refused (404, no manifest). The
  member's `target` still names the subject, so a consumer can fall back to its current revision
  (drop `revision`: the clash check then resolves the latest) -- with names that may have changed.
  The viewer neither warns about this nor falls back by itself today.
- A plain stored file has no revisions: a re-upload under the same key replaces what the member refers
  to.
- A copy would need a writer for every source format, including a provider's private CSG format that
  core deliberately has no reader or writer for (`ada/assets/manifest.py`), and would be a second
  source of truth that drifts from the first.

## Reading a member's source

1. `source.kind == "stored"`: read `source.key` through `source.scope`'s blob route. Pick the reader
   by `format`.
2. Find the member: by `element_id` where the reader indexes by the provider's node id; else by
   `element` with `path` (the names from below the model's root down to the element; match the path's
   tail if the source's hierarchy has more levels above).
3. `element: null` is the whole of what was loaded: the node `target.node` (else the subject), or the
   whole file.

## Who uses it in the viewer

- **Clash check (server checkers)** -- reads members from their sources on a worker
  (`ada.clash.group_model`): a node through its provider at `target.revision`, a file through core's
  reader. Members opened from a local file are left out with a note; a node member with no revision is
  pinned to `source.revision`. `source` rides along in the request and the server ignores it, so it is
  no part of a check's cache identity.
- **Clash check (convex contacts)** -- measures the loaded GLBs in the browser by design (it works on
  triangles); unchanged.
- **Request geometry…** -- asks a provider for an element member by `source.element_id` when it has
  one, instead of walking the Sources tree for it.
- **Load into scene** -- unchanged: it loads the derived geometry for display.
