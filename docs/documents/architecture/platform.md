# Viewer platform

The same `ada` library runs behind two server set-ups:

- **Desktop / notebook:** a single-process WebSocket server (`ada.comms.wsock`) that pushes
  scenes to a viewer as FlatBuffer messages.
- **Hosted:** a FastAPI REST service (`ada.comms.rest`) with a NATS JetStream job queue, a pool
  of workers, object storage, an optional PostgreSQL database and OIDC login.

## Deployment view

```mermaid
flowchart TB
    subgraph clients["Clients"]
        BR(["Browser viewer<br/>src/frontend"])
        CLI(["ada CLI"])
    end

    subgraph api["API pod — python -m ada.comms.rest"]
        APP["rest/app.py · create_app()<br/>FastAPI 'ada-py viewer API'"]
        ROUTES["rest/routes/*<br/>projects · storage · assets · fea · clash_check<br/>export_selection · plugins · procedural_models · admin_* · audit"]
        RPC["/api/rpc<br/>FlatBuffer envelope over REST"]
        JT["job_transport.py<br/>QueueJobTransport | LocalJobTransport"]
        BG["background tasks<br/>audit scheduler · plugin-job scheduler · issue bot<br/>worker prune · registry snapshot · job-KV cleanup"]
        APP --> ROUTES & RPC & BG
        ROUTES --> JT
    end

    subgraph workers["Worker pods — python -m ada.comms.rest.worker"]
        LOOP["worker/loop.py<br/>boot → register → pull"]
        POOLS["worker/pools.py<br/>capability pools"]
        REGW["worker/registration.py<br/>heartbeat + capabilities"]
        FMT["rest/formats/*<br/>one handler per job kind"]
        CONV["rest/converters/*<br/>(from_ext, to_ext) registry"]
        ISO["subprocess_convert.py<br/>forked, sampled child"]
        LOOP --> POOLS --> FMT --> CONV
        FMT --> ISO
        LOOP --> REGW
    end

    NATS[("NATS JetStream<br/>WORK_QUEUE stream<br/>ada.viewer.jobs.convert.CAP<br/>KV: job status · worker registry")]
    S3[("Object storage (obstore)<br/>S3 / Garage / local")]
    PG[("PostgreSQL (asyncpg)<br/>optional")]
    IDP[("OIDC provider<br/>JWKS")]

    BR -- "HTTPS /api/*" --> APP
    CLI -- "HTTPS + minted CLI token" --> APP
    BR -- "presigned GET/PUT" --> S3
    APP -- "verify JWT" --> IDP
    APP <--> PG
    APP <--> S3
    JT -- "publish job_id" --> NATS
    NATS -- "pull" --> POOLS
    REGW -- "heartbeat" --> NATS
    FMT <--> S3
    APP -- "poll status" --> NATS
```

## REST API (`ada.comms.rest`)

| Concern | Where | Notes |
|---|---|---|
| App factory | `app.py` (`create_app`) | Docs at `/api/docs`. Public: `/healthz`, `/api/config`. Everything else sits under `/api` behind `auth.current_user`. Conversion, utility, job polling (`/convert/{job_id}`), components and WASM audit routes are still defined in `app.py`. |
| Settings | `config.py` | `Settings` with `S3Config`, `LocalConfig`, `QueueConfig`, `AuthConfig`; read from `ADA_VIEWER_*` and `DATABASE_URL`. |
| Routers | `routes/*.py` | **Projects** (`/me`, `/projects`), **storage** (files, overlays, blobs, rename/move, derived, upload-url / upload-complete / upload-progress, download-url), **source nodes**, **assets** (providers, index, tree, attributes, geometry roll-up, build, publish, staging, unpublish), **fea** (`fea/artefacts`, `fea/artefact`, `fea/manifest`, `result-meta`), **clash** (clash-check, clash-detail, passes, checkers, geometry providers, connection specs), **export_selection**, **plugins / plugin_jobs / procedural_models**, **admin** (projects, users, settings, storage, compression, workers, corpora, plugin jobs) and **audit** (runs, schedules, perf + issue bot). Shared context lives in `deps.py` (`RestContext`). |
| Auth | `auth.py`, `scope.py` | Provider-agnostic OIDC JWT verification against the discovered JWKS. Admin role from a group claim. Users are upserted lazily by `sub`. The CLI uses minted tokens. With auth disabled, a synthetic local user is used. Access is scoped to *shared*, *project* or *user*. |
| Database | `db/pool.py`, `db/migrations.py`, `migrations/*.sql` | PostgreSQL via asyncpg. Numbered SQL migrations are applied under an advisory lock. Tables: users, projects, members, audit (log, runs, schedules, parity, issue rechecks), app settings, profiling stats, corpora, worker packages, procedural models / equipment types / system templates / engines, source nodes, plugin job schedules. **Optional:** without a DB the API runs shared-only and DB-backed routes return 503. |
| Storage | `storage.py` | obstore `S3Store` (S3, Garage) or `LocalStore` (`ADA_VIEWER_STORAGE_KIND`), with gzip helpers. Sources and derived artefacts share one key space (`_derived/...`). |

## Jobs

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant A as REST API
    participant Q as NATS JetStream
    participant W as Worker
    participant S as Object storage

    B->>A: POST /api/scopes/{scope}/convert (source key, target)
    A->>A: job_transport.build_transport()
    alt QueueJobTransport
        A->>Q: KV put job status = queued
        A->>Q: publish job_id on ada.viewer.jobs.convert.CAP
        W->>Q: pull from capability pool
        W->>S: read source
        W->>W: formats[kind] → converter / bake / clash …<br/>(optionally in a forked child)
        W->>S: write _derived/… artefacts
        W->>Q: KV put job status = done
    else LocalJobTransport (no NATS)
        A->>A: LocalJobRegistry runs it in a thread
        A->>S: write _derived/…
    end
    loop until done
        B->>A: GET /api/convert/{job_id}
        A->>Q: KV get status
    end
    B->>S: fetch artefacts (presigned URLs)
```

- **Transport** (`job_transport.py`): `QueueJobTransport` (NATS) or `LocalJobTransport`
  (in-process threads for a set of `LOCAL_FEATURES`: asset build/publish, clash
  check/detail, export selection, plugin jobs). The choice is made once at start-up.
- **Queue** (`queue.py`, `JobQueue`): a JetStream stream with WORK_QUEUE retention. The
  message body is just the job id. Status and the worker registry live in a KV bucket.
  Jobs go to capability subjects, so a worker only pulls what it can do.
- **Workers** (`rest/worker/`): `loop.py` boots, registers and pulls. `pools.py` has one pool
  per capability, fetches one job at a time and stops retrying a poison job after a limit.
  `registration.py` heartbeats the worker's capabilities (conversion matrix, utilities, clash
  passes/specs, asset concept providers), gated by `qualification.evaluate`. `routing.py`
  detects misrouted jobs.
- **Job kinds** (`rest/formats/__init__.py`): `convert` (fallback), `fea_artefacts`,
  `fea_meta`, `asset_build`, `asset_publish`, `component_build`, `procedural_*` (build,
  detail, relocations, xlsx import/export, model export, engine build), `equipment_bbox`,
  `plugin_job`, `clash_check`, `clash_check_asset`, `clash_check_group`, `clash_detail`,
  `clash_detail_group`, `export_selection`, `export_selection_asset`, `utility`, `parity`.
- **Converters** (`rest/converters/`): a `(from_ext, to_ext)` registry filled by
  `@converter`. Families: `ada_pairs` (passthrough, trimesh, ada-loadable sources),
  `ada_export` (GLB, IFC, Genie XML, GNX), `mesh_step` (STL, OBJ, STEP via OCC), `step_stream`
  (native STEP/IFC streams), `fea` (result decks), `pipelines` / `serializers` (tessellation
  engine options), `takeoff` (quantity take-off sidecar).
- **Isolation** (`subprocess_convert.run_isolated_convert`): conversions can run in a forked
  child, so a crash or memory blow-up cannot take the worker down. RSS, CPU and IO are
  sampled for the audit records.

## Desktop WebSocket server (`ada.comms.wsock`)

```mermaid
flowchart LR
    PY["Python: obj.show() / renderer"] --> SRV["wsock/server.py<br/>WebSocketAsyncServer"]
    SRV <-- "FlatBuffer wsock.Message" --> FE["viewer<br/>utils/comms/ws_comms.ts"]
    SRV --> H["msg_handling/default_on_message.py<br/>branch on CommandTypeDC"]
    H --> C1["update_scene · update_server"]
    H --> C2["list / view / delete_file_object"]
    H --> C3["run_procedure · list_procedures<br/>load / save_procedural_model"]
    H --> C4["mesh_info_callback · shutdown_server<br/>start_local_app · start_separate_node_editor"]
    WEB["web/serve.py<br/>serves the SPA, injects WS port"] --> FE
```

The messages are defined once in `src/flatbuffers/schemas/*.fbs` (root: `message.fbs`).
`src/flatbuffers/update_flatbuffers.py` runs `flatc` and the generators that write the Python
dataclasses, serialisers and deserialisers (`src/ada/comms/fb/`) and the TypeScript bindings
(`src/frontend/src/flatbuffers/`). The REST `/api/rpc` endpoint accepts the same envelope, so
the frontend can use either transport.

## Domain services

### Assets (`ada.assets`)

A tree-shaped asset store fed by **providers**: collection → subject → revision → files.

| Module | Role |
|---|---|
| `publish.py` | The provider plans, the core writes. The core stamps `published_by` and writes manifests last. |
| `index.py` | `fold_listing`: one storage prefix listing folded into the tree. |
| `rollup.py` | Geometry roll-up over the whole tree. |
| `projection.py` | The columnar `hierarchy.json` spine the viewer's tree reads. |
| `ifc/`, `builders`, `publishers`, `concepts`, `unpublish`, `keys` | The IFC provider and the build/publish/unpublish plumbing. |

`routes/assets.py` exposes the tree. Builds and publishes run as `asset_build` /
`asset_publish` jobs.

### Clash and joints (`ada.clash`)

Core identifies and types joints; generators detail them.

```mermaid
flowchart LR
    SRC["model source<br/>(never the GLB)"] --> ID["identify.py<br/>beam–beam (Connections.find)<br/>plate–beam · plate–plate"]
    ID --> CL["classify.py<br/>joint typing"]
    CL --> DET["detail.py<br/>generators"]
    PASS["passes.py<br/>pass registry by capability"] --> ID
    GRP["group.py · group_model.py<br/>named member groups"] --> ID
    GEO["geometry_source.py<br/>match members across<br/>published assets"] --> SRC
    FA["from_asset.py"] --> SRC
```

The REST routes in `routes/clash_check.py` enqueue `clash_*` jobs. Workers advertise the
passes and connection specs they support (`registration.py`).

### Plugins (`ada.plugins`)

The Python twin of the frontend plugin registry: `register_plugin_backend`,
`discover_plugins`, `plugin_backend_specs`, `register_plugin_artefact_contributor`,
`request_worker_capabilities`, plus external model providers. Plugins are loaded from
`ADA_WORKER_PRELOAD` modules and an entry-point group. `rest/plugin_registry.py` is the only
place the REST side touches the registry.

### Audit and issue bot

Every conversion leaves an audit row: its outcome, conversion provenance and the profiler
summary parsed from the child process log (`worker/audit.py`). Admins schedule audit runs on a
cron (`routes/admin_audit_schedules.py`, fired by the scheduler background task) and inspect
runs and performance (`admin_audit_runs.py`, `admin_audit_perf.py`). `audit_issue.py`
fingerprints failures and files or updates issues on GitHub or Forgejo (`issue_client.py`).
The `ada audit …` CLI commands fetch and reproduce runs.

## Deployment

| Artifact | Built from | Runs |
|---|---|---|
| Viewer / API image | `deploy/Dockerfile.viewer`: adacpp WASM wheel → adapy wheel → `npm run build:serve` → pixi `viewer-api-slim` env → debian-slim | `python -m ada.comms.rest` on :8080 (also serves the SPA) |
| Worker image | `deploy/Dockerfile.worker`: pixi `viewer-api` env, optional adacpp-from-source overlay | `pixi run -e viewer-api viewer-worker` |
| Docs image | `deploy/Dockerfile.docs`: pixi `docs` env → FEA report + Zensical site → nginx-unprivileged | Static site on :8080 |
| `*-fast` variants | Re-layer only changed `ada` sources on a published base | Same as above |
| Local stack | `deploy/docker-compose.dev.yml` | NATS (`-js`), api and worker, local storage, no DB |
| Cluster | `deploy/helm/adapy-viewer` | api, workers (+ extras), NATS, PostgreSQL, Garage (S3), ingress |

CI: `.forgejo/workflows/build.yaml` detects changed paths, builds the viewer, worker and
docs images (full or fast) and bumps image tags in the GitOps manifests. GitHub Actions run
the test suites, publish images to GHCR, release to PyPI, track profiling, and publish these
docs to GitHub Pages (`ci-pages.yml`).
