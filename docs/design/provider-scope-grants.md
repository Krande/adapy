# Provider data is global; scopes get grants

Status: design, plus phase-1 scaffolding (migration 045, `Scope.providers()`, `db/provider_grants.py`, `/api/admin/provider-grants`).

## Goal

- Provider data is stored once, keyed by provider and project, under no user or project scope. That covers the attributes in Postgres and the CSG, mesh and tree exports in S3.
- Extraction jobs, sweeps and crons are admin operations. Where a job is started from never changes where its data lands.
- An admin maps scope → dataset (provider, project) with separate permissions. Members read through the grant, and nothing is copied into the scope.

"Collection" in the code is the provider's project (an E3D project or a web3d project). Below it is called a *dataset* `(provider, collection)`.

## 1. Current coupling (what ties provider data to the launching scope)

| # | Where | What |
|---|---|---|
| 1 | `storage.py:251-264` (`_scope_prefix`, `_full_key`), `scope.py` `Scope.prefix()` | Every blob key is `<scope prefix>/<key>`, and asset keys are `assets/<collection>/<subject>/<revision>/<file>` (`ada/assets/keys.py:42-43`). Provider is not in the key. |
| 2 | `routes/plugin_jobs.py:412,429-432` | `POST /plugins/{id}/jobs?scope=` defaults to `shared`. The job runs in whatever scope the caller names. |
| 3 | `routes/plugin_jobs.py:153-171` (`enqueue_plugin_job`) | `JobRequest(scope=scope_obj)`. The worker rebuilds it (`worker/state.py:21`, `job_transport.py:299-300`). |
| 4 | `formats/plugin.py:87,166,178,278` | The plugin gets a storage facade bound to the job scope, `scope=`, and a source-nodes recorder bound to the job scope. The summary is written into that scope. |
| 5 | `routes/assets.py:916-1037` (`api_asset_publish`, `submit_asset_publish`), `formats/asset_publish.py:121-131,147-167` | Publish reads the staging and writes manifests/artefacts into the job scope. `publisher.derive(scope, ...)` receives it. |
| 6 | `db/attribute_index.py:449,536-557`; migration 037 PK `(scope, provider, collection)` | Attribute partitions are keyed by the publishing scope. The "one copy" dedup only folds toward `shared` (`:54-63,417-446,569`). |
| 7 | `routes/asset_sweeps.py:555-604` (admin start needs `scope`), `:207-264` (`start_sweep`), `:418-454` (options, enqueue, summary read in `scope_obj`), `:482-485` (publish in `scope_obj`), `:194-204` (provider options read from `assets/_options/<c>.json` in that scope) | Admin sweeps are run *for a scope* and publish into it. |
| 8 | migration 029 `plugin_job_schedules.scope`; `routes/admin_plugin_jobs.py:249-251,274-279,358-368` | Crons resolve a stored scope, guard in-flight runs per scope and enqueue into it. |
| 9 | `routes/asset_schedules.py:391-396` (`scope_key`), `:413-424` (settings per scope), `:430-519` (change check and runs per scope); migration 035 `asset_change_runs.scope` | Change checks run in, and are listed per, scope. |
| 10 | migration 028 unique `(scope, source, node_ref)`; `worker/source_nodes.py:43-51,145-149`; `routes/source_nodes.py:72,224` | The change feed and its watermark (the E3D cursor) are per scope. Two scopes means two cursors and two extractions. |
| 11 | `routes/assets.py:186-731` (providers, index, geometry roll-up, tree, attributes, delivery, build) | All reads come from `scope_obj` storage. Builds cache under the scope (`:875`) and run as scope jobs (`:882`). Live providers receive the reader's scope (`:420,627,685`). |
| 12 | `routes/attribute_search.py:62-75,134-141`; `db/attribute_index.py:1683-1735` | Search reads its own scope plus shares, and a share is readable only if the caller can reach the *origin* scope. |
| 13 | `routes/asset_exports.py:193-219,748-806`; `search_export.py:263`; `formats/export_search.py:112-119` | Export reads a hit's attributes, tree and geometry from the hit's origin scope. Design geometry comes from `GEOMETRY_PROVIDER` *in the same storage scope*. Tree/geometry requests publish into the origin. |
| 14 | `routes/assets.py:150-183` | Provider display names are per scope. |
| 15 | Frontend: `services/assetScopeCollections.ts:21-25,43` (`public.assets.scope_collections`, "a picker filter, not a permission"); `services/providerOptions.ts:20-56`; `assets/collectionRequest.ts:216-284` (request, then publish, both in the picker scope); `components/admin/ProvidersTab.tsx:218,333`; `ProviderSchedulesSection.tsx:176-206` (scope dropdown on schedules and sweeps); `services/providerRescan.ts:55,70` (hard-coded `shared`); `AssetsTab.tsx:1603` | The UI binds every provider action to the current scope. |
| 16 | Plugins: E3D `adapy_job.py:916,936` (sweep state is one file per scope); `:2186-2211,2393` (source_nodes `e3d:<PROJECT>` per scope); `core_provider.py:129-178` (reads earlier revisions and the *other* provider's index in the same storage). web3d `asset_provider.py:116,1364` (`_derived/web3d/*` caches per scope); `:202-1264` ("unchanged" decided from earlier publishes in the scope); `mirror.py:1266,1345-1404` (reads `scope_collections` to pick which projects to mirror). | Plugins never hard-code a scope. They read earlier state through the facade, so they follow the facade wherever it points. |

Consequence: one project extracted from three scopes means three S3 copies, three attribute partitions, three change cursors and three licensed-seat runs. Migrations 039/040 (shares) and the fold-to-shared dedup are partial workarounds for this.

## 2. Data model

**Provider store.** There is one storage scope, `Scope.providers()`, with prefix `providers` and wire `providers`. The key grammar inside it is unchanged:

```
<base>/providers/assets/<collection>/<subject>/<revision>/<file>     published (manifest names the provider)
<base>/providers/assets/_staging/<id>/<file>                         provider staging
<base>/providers/assets/_options/<collection>.json                   admin's per-collection provider options
<base>/providers/_derived/assets/<provider>/<collection>/...         builds, roll-ups, publish summaries
<base>/providers/_derived/plugin_jobs/<plugin>/<hash>.json           job summaries
```

The store is a single namespace rather than `providers/<provider>/...` because providers of one project have to sit side by side. web3d reads E3D's index to map ids. The export builds IFC for a web3d hit from the E3D CSG of the same collection. A collection tree can be mixed. All of these read one storage scope today (`search_export.resolve_geometry`, `core_provider._foreign_index_ids`), and they keep working unchanged. The provider is recorded in each manifest and in every DB key. (Open question 1.)

**Postgres.** There is no new data table. Provider data uses the existing tables with `scope = 'providers'`: `attr_collections`, `attr_values`, `source_nodes`, `asset_change_runs`, `asset_sweep_runs` and `plugin_job_schedules`. Global uniqueness then comes from the existing keys `(scope, provider, collection)` and `(scope, source, node_ref)`. New tables:

- `provider_grants` (migration 045, **done**): `(target_scope, provider, collection NULL = all)` is unique. It has `attributes`, `geometry` and `request` booleans, plus `granted_by`, `granted_at` and `note`. CHECK constraints keep the store and corpora from being targets. Indexes cover both directions.
- `provider_store_migration` (phase 4): one row per moved dataset `(provider, collection, from_scope, revision, blobs, partition, grants_created, state)`. The rollback is driven from it.

**What counts as provider data.** A publish whose `provider` is advertised as `asset_provider_id` by a live plugin spec, or is listed in `ADA_STORE_PROVIDERS`. User uploads published under the built-in `published` provider stay in the user's scope.

## 3. API

Admin (implemented in phase 1, under `/api/admin`):

- `GET /provider-grants?target_scope=&provider=&collection=` lists grants. A `collection` filter also returns the provider-wide grants that cover it.
- `PUT /provider-grants` with `{target_scope, provider, collection?, attributes?, geometry?, request?, note?}` creates a grant or replaces its permissions. The defaults are attributes and geometry on, request off.
- `DELETE /provider-grants` with `{target_scope, provider, collection?}` revokes.

Admin (later phases):

- `/asset-sweeps`, `/asset-schedules` and `/plugin-jobs/schedules`: `scope` is no longer required. It is accepted and ignored for asset providers, and the response says `scope: "providers"`.
- `GET /provider-datasets` returns every `(provider, collection)` in the store with revision, size, last sweep, change-feed watermark and its grants. This is the admin's main table.
- `POST /provider-store/migrate` with `{plan|apply|rollback, providers?, collections?, run_id?}`. Plan is a dry run unless told otherwise, as with `attr-prune-revisions`.

Scope routes keep their paths. Callers do not change URLs, and the server resolves the reads:

- `/scopes/{s}/assets/*`, `/scopes/{s}/assets/search*`, `/scopes/{s}/assets/search/export*`, `/scopes/{s}/source-nodes`, `/scopes/{s}/asset-changes/runs*`.
- New: `GET /scopes/{s}/assets/blob/{provider}/{collection}?key=` streams a granted dataset's blob (mesh, built GLB, hierarchy). `delivery` and `build` return `blob_url` pointing at it, so the viewer never needs to name the store.
- New, read-only for members: `GET /scopes/{s}/provider-grants` lists what the scope may read, for the Sources tab. Worker tokens get `GET /api/provider-grants/datasets?provider=`, which replaces web3d's read of `scope_collections`.
- `POST /plugins/{id}/jobs` for an asset-provider plugin always runs in the store. `?scope=` is then only the *requesting* scope, used for the grant check and the audit row. The response carries `scope: "providers"` so the client reads the summary from there.

## 4. Reads through grants

There is one resolver in `routes/deps.py`:

```
resolve_dataset(pool, user, scope, provider, collection, permission) -> Scope
  user is admin and scope == providers                → providers
  grant_access(scope.prefix(), provider, collection) allows permission
      and the store holds the dataset                 → providers
  otherwise                                           → scope   (legacy local copy, until phase 5)
```

"The store holds it" means the store has an `attr_collections` row (for attributes) or an `assets/<collection>/` listing (for geometry), cached briefly. A grant therefore *shadows* a local copy only once the store has the data. Before migration this changes nothing, and after migration the stale local copy is never read again.

- **Search**: `db/attribute_index._partitions` gains `granted=[(provider, collection|None)]` (from `granted_datasets(..., "attributes")`). It reads the matching `scope='providers'` partitions and drops the scope's own partition of the same `(provider, collection)`. Rows carry `scope: "providers"`, `scope_wire: null` and `granted: true`. `readable_share_origins` is unchanged for user-to-user shares. The admin's `all_scopes` becomes "search the store".
- **Viewer** (tree, attributes, delivery, build, index, geometry roll-up, providers list): provider-addressed routes call `resolve_dataset(..., "geometry")` with the path's provider. Collection-addressed routes (index, roll-up, `published`/any) resolve to the store when any provider of the collection is geometry-granted, and filter what they return to granted providers (the roll-up is already per provider; the index filters by manifest provider). Builds of granted datasets run as store jobs and cache in the store's `_derived/`, so every granted scope shares one built GLB.
- **Export**: a hit from the store has `origin = "providers"`. `refuse_unreadable_hits` accepts it only if the hit's `(provider, collection)` has an `attributes` grant. Design geometry for any hit, web3d hits included, is the `GEOMETRY_PROVIDER` CSG of the same collection in the store. It needs a `geometry` grant on `(GEOMETRY_PROVIDER, collection)`, otherwise the file reports `no_geometry` with `reason: "not_granted"`. web3d is never a geometry source, and non-mesh E3D primitives still export as exact primitives. Files are still written into the user's target scope, as today.
- **Change feed** (`source-nodes`, `asset-changes/runs`): these read `scope='providers'` rows when the scope holds a grant for the provider.

## 5. Jobs and crons become scope-independent

- `job_scope(plugin_spec)` returns `Scope.providers()` when the spec declares `asset_provider_id`. `enqueue_plugin_job`, `submit_asset_publish`, `start_sweep`, `start_change_check` and `plugin_schedule_fire` all use it, so no caller can pick the scope for provider data.
- Sweeps and schedules carry no scope. Each `(provider, collection, schedule_job)` has one live schedule, and the in-flight guard becomes per plugin (`admin_plugin_jobs.py:274`). Provider options live once, at `providers/assets/_options/<c>.json`, and are edited by admins.
- The source-nodes recorder is bound to the store, so each `(source, node)` has one cursor. E3D's sweep-state file becomes one file instead of one per scope.
- A member-initiated request (Sources "Request", node request, export's "request missing geometry/trees") is allowed when the user is an admin, or when the grant has `request` and the plugin's own `requires_admin` gate passes. The job runs in the store. The run row records `requested_from = <scope>` for display.
- Transitional (phase 3 only, flag `ADA_PROVIDER_AUTOGRANT=1`): when a job is requested from scope S, S receives a grant on that dataset if it has none. Existing workflows then keep seeing their data while admins set up grants.

## 6. Authorization

| Who | Can |
|---|---|
| Admin | Read and write the store directly (`/scopes/providers/...`, as for a corpus). Manage grants, sweeps, schedules, provider options and migration. |
| Member of S (`can_access(S)`), with a grant `attributes` | Search, keys, values, export attributes for that dataset. |
| … with `geometry` | Tree, attributes doc, delivery, blob, build (the shared cache is written by core, never by the caller), design geometry in export. |
| … with `request` | Trigger the provider's collection, node and tree requests for that dataset. The output goes to the store. |
| Anyone else | Nothing. A non-admin cannot name `providers` (`can_access` returns False, already done). |

- Grants on `shared` apply to every authenticated user. Grants on an archived project are inert, because `can_access` already fails.
- Revocation is immediate, since access is checked per request. Files already exported into a scope stay there as the user's output.
- A grant never copies data, and nothing a member does can write provider data except a `request` (provider job) or a core-composed derived cache.

## 7. Migration and dedup

The tool is an admin endpoint and CLI: `plan` → `apply` → `verify` → (later) `purge`, or `rollback`. It is idempotent and resumable per dataset, and no scoped copy is deleted before `purge`.

1. **Inventory.** For every scope (`shared`, `projects/*`, `users/*`), list `assets/` and read the manifests, giving `(provider, collection, subject, revision)` per scope. Read `attr_collections` grouped by `(provider, collection)` with revision and ingest time. Also read `source_nodes` per `(scope, source)`, `plugin_job_schedules` with `asset_provider`, the per-scope `assets/_options/*.json`, `attr_shares`/`attr_provider_shares`, and `public.assets.scope_collections`.
2. **Canonical copy per dataset.** Use the existing `newest_copy` rule (`attribute_index.py:1914`): newest numeric revision, else latest ingest. Flag a dataset as **conflict** when its scopes hold different provider options documents. An admin picks the winner, and nothing moves until then.
3. **Blobs.** From the canonical scope, server-side copy each revision prefix `assets/<c>/<s>/<r>/` whose manifest names the provider into `providers/assets/...`. Copy every revision, because leaves reference older spines through `hierarchy_revision`, and let retention prune afterwards. Copy artefacts first and manifests last, keeping the publish ordering invariant. Skip identical keys (same size/etag). Do not copy `_staging`, `_derived` or `_publish`; those are caches and rebuild on demand.
4. **Attributes.** Re-key the canonical partition in one transaction: `UPDATE attr_collections SET scope='providers'`, keeping `part_key` and `partition_name`. Small code change: the next ingest drops the partition named in the existing row when its name differs from the computed one. Record the old scope in the migration log. Other scopes' partitions stay in place, shadowed by the grant (§4).
5. **Change feed.** `INSERT … SELECT 'providers', source, node_ref, … ON CONFLICT DO UPDATE` with `GREATEST(last_changed_at)` and `GREATEST(observed_at)`, so the merged watermark is the furthest any scope got.
6. **Schedules.** Collapse to one live schedule per `(provider, collection, job)`: keep the most recently fired, set its scope to `providers`, and archive the rest with a note. Merge provider options from the canonical scope into `providers/assets/_options/`.
7. **Grants.** For every scope that held a copy, every share target of one, and every explicit `scope_collections` entry, create a grant (`attributes`, `geometry`; `request` set when that scope could request before). `granted_by = "migration:<run id>"`.
8. **Verify.** For each grant: the search count per partition before and after, a tree root and one delivery resolving through the grant, and an export dry-run (preflight) for a sample hit with design geometry present.
9. **Purge** (phase 5, after a soak). Delete the shadowed scoped copies (blobs whose manifest provider is a store provider, their partitions, their `source_nodes` rows, archived schedules) with the existing drop and storage helpers. This step is irreversible, has a dry run by default, and is per provider.

**Rollback** (any time before purge): delete grants whose `granted_by` is `migration:<run>`; re-key each moved partition back to its logged scope; delete the run's `source_nodes`/schedule changes from the log; turn writes back to scoped (`ADA_PROVIDER_STORE_WRITES=0`). Reads then fall back to the untouched local copies. The blobs copied into the store can stay; with no grants they are invisible.

## 8. Plugin contract

There is no signature change. `job_entrypoint(options, *, storage, scope, on_progress, derived_prefix, cancel_event?, source_nodes?)` keeps its shape, but for an asset-provider job `scope` is `Scope.providers()` and the facade and recorder are bound to the store. Both plugins already go only through the facade, so staging, "unchanged" detection, foreign-index reads, the E3D sweep state and the `_derived/web3d/*` caches move with it. `publisher.derive(scope, …)` and the live `delivery`/`attributes`/`hierarchy(scope, …)` receive the store scope, so a provider must not branch on the reader's scope (neither does today).

Plugin-side changes, to be made in those repos and not here:

- **web3d** `mirror.py:1266,1345-1404`: read the projects to mirror from `GET /api/provider-grants/datasets?provider=<id>`, or from the store's dataset list, instead of `public.assets.scope_collections`. Core keeps writing that setting from grants during phases 2-4, so nothing breaks before the switch.
- **E3D** CLI publish (`assets/publish.py:392-478`, `viewer_staging.py:122-143`): target `ADAPY_API_SCOPE=providers` with an admin token. The comments at `adapy_job.py:3510` and `:2192-2198` ("one state per scope") become "one state".
- Both specs: nothing to add. `requires_admin` keeps meaning "who may ask", and the new `request` grant is checked as well as it, never instead of it.

## 9. Phases (each shippable alone)

1. **Grants and the store scope.** This commit: migration 045, `Scope.providers()` (admin-only, `parse_scope("providers")`, worker reconstruction), `db/provider_grants.py`, `/api/admin/provider-grants`, tests. Next in this phase: the Admin → Providers → Access UI. No read or write changes yet.
2. **Read through grants.** `resolve_dataset`, search `granted`, export origin `providers` and the geometry-grant rule, the blob route, and `GET /scopes/{s}/provider-grants`. With the store empty, behaviour is unchanged.
3. **Writes go to the store.** `job_scope()`, sweeps and schedules without scope, provider options in the store, the change feed in the store, member requests via `request`, transitional auto-grant. Frontend: drop the scope dropdowns, read job summaries from the returned scope, fix the `providerRescan` hard-coded `shared`. Behind `ADA_PROVIDER_STORE_WRITES` (default on once tested). New extractions land once, and old copies stay readable through the fallback.
4. **Migrate and dedup** the legacy copies (§7), with rollback. Ship the web3d mirror change in this phase.
5. **Clean up.** Purge shadowed copies; retire `scope_collections`, provider-data attr shares and the fold-to-shared dedup; drop the per-scope provider aliases (or keep them as overrides); make `scope` columns of the run tables nullable and documented as "requested from".

## 10. Open questions

1. **Layout.** Use one co-located namespace `providers/assets/<project>/…` (recommended, so cross-provider joins keep working), or per-provider prefixes `providers/<provider>/assets/<project>/…`? The second needs a separate geometry origin for exports and for web3d's foreign-index read.
2. **Permissions.** Are three flags (`attributes`, `geometry`, `request`) right, with provider-wide and collection grants adding up?
3. **Who may request extraction.** Should members with a `request` grant be able to trigger it, or admins only? The user's model says admin-only, but the export's "request missing geometry" and the Sources "Request" are member features today.
4. **Divergent per-scope provider options.** If two scopes extract one project with different options (or different credentials), is one configuration per `(provider, project)` acceptable, with an admin resolving the conflicts?
5. **Shared derived cache.** Builds triggered by members write core-composed caches into the store. Is that acceptable?
6. **Grants to personal scopes and to `shared`.** Allow both, as now?
7. **Seeding grants from `scope_collections`.** For `shared` and projects, "missing" there means unrestricted. Should migration grant everything such scopes could request, or only what they actually held?
8. **Revision history.** Copy all revisions (safe, temporarily larger) or only the latest plus the spines it references?
