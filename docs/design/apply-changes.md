# Apply changes: keep provider data current, one element at a time

Status: design, being built on `feat/provider-store` (core) and `feat/apply-changes` (asa-e3d-connect).

## Goal

A scheduled job that keeps the provider store (`docs/design/provider-scope-grants.md`) current with
its provider source by applying **only what changed since the last run, at the lowest level the
change happened**. If one GENSEC changed, exactly that GENSEC's rows and blobs are rewritten: its
geometry, its attribute rows, its bounds, its relations -- nothing at its level, its SITE or its
project. Same for every element kind.

It is aimed at the E3D provider (asa-e3d-connect) and its sharded worker pool, which can answer
"what changed since a cursor" per element. A provider that cannot (web3d: one GLB per SITE) simply
does not declare it.

## 1. The declaration (provider spec)

A new `asset_schedules` kind, `apply-changes`:

```python
{
    "id": "apply-changes",
    "kind": "apply-changes",
    "label": "Apply E3D changes",
    "description": "...",
    "options": {"action": "apply-changes", "schema_version": N},   # fixed job options
    "collection_option": "project",
    "settings": [{"name": "apply_targets"}, {"name": "exclude_users", "choices": "change_users"}],
    "parallel": 5,               # lanes, capped by core at the live workers serving the provider
    "fetch_timeout_s": 3600,     # optional
}
```

and in `job_options`, a MULTI-choice setting the Scheduled jobs form renders as checkboxes:

```python
{"name": "apply_targets", "type": "enum", "multiple": True, "title": "Apply to",
 "enum": ["attributes", "geometry", "bounds", "relations"],
 "labels": {"attributes": "Attributes", "geometry": "Geometry", "bounds": "Node bounds",
            "relations": "Relations"},
 "default": ["attributes", "geometry", "bounds", "relations"],
 "applies_to": ["apply-changes"]}
```

A provider may offer a subset of the enum. The tree (hierarchy) is NOT a target: when elements are
inserted, deleted or moved the tree is always patched, whatever is ticked, or every other target
would hang off nodes the tree does not have.

A core that does not know the kind drops the entry with a log line (as for `sweep`).

## 2. The schedule and the run (core)

- Admin -> Providers -> Scheduled jobs offers the entry like any other: frequency, the targets as
  checkboxes, `exclude_users`, and the collection -- one project, or **all** (empty: every
  collection the provider offers, driven like a sweep, `parallel` lanes). No scope (the provider
  store).
- **Core owns the cursor**, per `(provider, collection)`: table `asset_apply_cursors` (migration
  046) with the cursor the provider returned (`next_cursor`, an opaque string; E3D sends an ISO
  date), when it was applied and by which run. It advances ONLY after every patch of that
  collection's answer has been written. A run that fails part-way leaves the cursor where it was;
  the next run asks the same question again. That is safe because every patch is an idempotent
  upsert or delete.
- Per collection, one ordinary provider job (routed to the provider's shard as any request) with
  options: the fixed options + `project` + `apply_targets` (list) + `since` (the stored cursor, or
  null) + `exclude_users` + `requested_at`.
- `since` null is the FIRST run: the provider answers a baseline -- `next_cursor` = now, nothing
  staged -- and core records the cursor. Applying changes presumes a full export already in the
  store; the first run only says where "since" starts.
- Runs are recorded like sweeps (one run row, one item per collection, timings per phase), so the
  Providers tab shows them in the existing runs table with per-collection counts.
- **No full export, no apply.** Before asking the provider, core checks that the store holds a
  complete revision of the collection's index subject (`assets/<c>/<c>/<rev>/` with its manifest)
  published by this provider -- matched case-insensitively, one listing per run. A collection
  without one is not sent to the provider and gets no cursor: its item ends `no_full_export`
  ("<collection> has not had a full project export yet") and the run's `warnings` name it. The
  provider's own "no baseline" answer, and a refused tree or index patch, are treated the same and
  keep the cursor. Core's own setting `missing_full_export` (on every apply-changes entry, never
  sent to the provider) picks `warn` (default) or `export`: queue the provider's full export
  (its collection request, driven as a sweep, `requested_via: apply-changes`) for exactly those
  collections once the run has finished; the two runs link through their `summary`
  (`queued_full_exports` / `apply_changes_run_id`).

## 3. The job's answer (provider -> core)

The summary JSON at the job's derived key:

```json
{
  "apply_changes": {
    "schema": "ada.assets/apply-changes@1",
    "since": "2026-10-29T02:00:00Z",
    "next_cursor": "2026-10-30T02:00:03Z",
    "baseline": false,
    "targets": ["attributes", "geometry", "bounds", "relations"],
    "counts": {"changed": 3, "inserted": 1, "deleted": 2, "unplaceable": 0},
    "nothing": false
  },
  "asset_staging_id": "…",          // absent when nothing (or baseline)
  "timings": {"changes_ms": 0, "export_ms": 0, "stage_ms": 0},
  "warnings": []
}
```

`counts` are ELEMENTS at their own level -- never their level or SITE.

### Staging roles (each present only when its target is ticked and something changed)

| role (filename) | content | applied by core as |
| --- | --- | --- |
| the provider's own publish input (e.g. `csg.db`) | geometry of the CHANGED and INSERTED elements only, each element its OWN subject | an ordinary publish by the provider's publisher: one new subject-revision per element |
| `attribute-index-patch.ndjson.gz` | header line, then one line per upserted node in the `attribute-index` row format, or `{"node": "<id>", "deleted": true}` | upsert/delete of exactly those nodes' rows in the existing partition |
| `node-bounds-patch.ndjson.gz` | header line, then `node-bounds` rows, or `{"node": "<id>", "deleted": true}` | upsert/delete of those nodes' bounds |
| `node-relations-patch.ndjson.gz` | header line, then `node-relations` rows (a node's lines REPLACE its relations as source), or `{"node": "<id>", "deleted": true}` | replace/delete of those nodes' relations |
| `hierarchy-patch.json` | `{"schema": "ada.assets/hierarchy-patch@1", "collection", "cols": [spine cols], "upsert": [rows], "delete": [ids]}` -- inserted and moved nodes as full spine rows (their new parent in `parent`), deleted ids | an overlay over the published spines (section 4) |

Patch header line: `{"schema": "ada.assets/<kind>-patch@1", "provider", "collection", "since",
"next_cursor"}`. Node ids are the SAME ids the provider's full tree publish uses (one id function).
A patch for a set that does not exist yet (no full ingest of that provider/collection) is refused
for that role with a warning ("no baseline; run a full export first") and does NOT block the rest.

## 4. Reading patched data (core)

- **Tree**: hierarchy patches are stored durably per collection, ordered, and the tree route
  overlays every patch newer than the spine revision it reads (upserts replace/insert rows by id,
  deletes drop the row and its subtree; children counts follow). A newer full spine publish
  supersedes older patches (they are ignored, and may be pruned). The geometry roll-up reads the
  same overlay. A spine is never rewritten to apply a patch.
- **Geometry**: an element published as its own subject is newer than the level-wide publish that
  also covers it. The viewer already resolves a node's covering subject as the nearest one -- the
  element's own manifest wins for that element. For a BUILD of an ancestor, core passes the
  builder `exclude_nodes`: the descendants that have a newer own publish, and the ones a hierarchy
  patch deleted since the covering revision. It is part of the fingerprint, and the build answer
  carries `overrides: [{node, subject, revision}]`, which the viewer loads beside the build. The
  provider's builder skips excluded subtrees. So the ancestor's mesh never shows an element's old
  geometry next to its new one, and never shows a deleted element.
- **Attributes, bounds, relations**: patched in place in the provider store's sets; every reader
  (search, export, box request) sees the change at once.

## 5. What the E3D provider does per job

1. `changes-since` with the given `since` (and `exclude_users`): the per-element delta.
2. Reduce it to the lowest denominator: each reported element itself. An owner appears only when
   it was itself reported as changed; an owner touched only through a child (reorder, insert,
   delete) is a hierarchy patch, not a re-export. Nothing is escalated to its level or SITE.
3. Per ticked target, export ONLY those elements: CSG for changed+inserted (each its own subject),
   their attribute rows, their bounds (from the exported geometry), their relations; the hierarchy
   patch for inserted / deleted / moved elements.
4. Stage the roles above and answer section 3. With nothing changed: `nothing: true`, no staging,
   and the cursor still advances.
5. The builder honours `exclude_nodes`.

## 6. Not in scope

- web3d: it does not declare the kind; its element-level route (GLB draw-range diff) is not built.
- Compacting patches into a new spine is the next full export's job, not this one.
