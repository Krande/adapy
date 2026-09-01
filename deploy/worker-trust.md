# Worker trust: authentication, admission, and capability qualification

A design for **who is allowed to be a worker**, **what the API is willing to
believe from one**, and **when a worker should decline to advertise a capability
it is no longer fit to serve**.

Written because we want to run workers *outside* the cluster — a workstation
that joins a capability pool because it holds something no cluster node can (a
licensed seat, a device, a dataset). But every problem below already exists for
the in-cluster pools. The external worker does not introduce these gaps; it
removes the network boundary that has been quietly standing in for all three
controls.

---

## 1. What is trusted today

`JobQueue.connect()` is `await nats.connect(self._cfg.url)` — **no credentials,
no TLS, no accounts**. The chart deploys a plain `nats -js` on a ClusterIP
service (`helm/adapy-viewer/templates/nats.yaml`). The only thing standing
between an actor and the job queue is the ability to open a TCP connection to
port 4222.

Anything that can do that can:

1. **Take other pools' jobs.** Subscribe to `ada.viewer.jobs.convert.<cap>` for
   any capability. Jobs carry scope ids and storage keys.
2. **Impersonate a plugin.** The worker registry is a KV bucket any client can
   write. `_live_worker_specs()` in `app.py` unions `plugin_specs` from every
   non-stale registry row, **keyed by slug, last writer wins**, with no check on
   who wrote the row. Writing one row is enough to (a) put an arbitrary entry in
   every user's `GET /api/plugins`, and (b) re-point an existing plugin id's
   `worker_capability` at a pool the writer controls — after which
   `POST /api/plugins/report-builder/jobs` routes that plugin's work, and the
   options it carries, to the writer.
3. **Falsify job outcomes.** Job status lives in the same KV bucket under the
   job id. Any client can mark any job `done` and point `derived_key` wherever
   it likes.
4. **Administer JetStream.** Workers today call the same `connect()` the API
   does, which does `add_stream` / `update_stream` and deletes legacy consumers.
   So nothing in the current design even *distinguishes* a worker from an
   administrator — there is no lesser privilege to grant.

Separately, and independent of NATS: a worker holds **bucket-wide object store
credentials**, because `Storage` talks to S3/Garage directly and there is no
REST-mediated storage kind.

### The three problems this splits into

| | Question | Today |
| --- | --- | --- |
| **Authentication** | Is this connection a principal we know? | No |
| **Authorization** | What may that principal do on the bus? | Everything |
| **Admission** | What will the API *believe* from it? | Everything it says |

They are separable, and the third is the one that actually stops the plugin
hijack in (2): a legitimately-credentialed external worker could still *claim*
`report-builder` if it can write the registry. Transport auth alone does not
answer "what may this worker claim to be".

## 2. Design A — one credential model, k8s included

**Do not build an external-only auth path.** The in-cluster worker being trusted
by network position is precisely what makes (2), (3) and (4) reachable from any
compromised pod in the namespace, and a second mechanism for outsiders would
leave that standing while doubling what we have to maintain. One model, and the
k8s workers get credentials from a Secret exactly as the VM gets them from a
file. The external worker is then not a special case — just a user with a
narrower permission set.

### Users and permissions

Plain NATS `accounts`/`users` in the server config is sufficient at this scale;
NKEY/JWT (`nsc`) buys per-worker expiry and rotation and is the better end state
if the external fleet grows past a handful.

```
api            pub/sub  ada.viewer.jobs.convert.>          # publish jobs
               JS API   full on ADA_VIEWER_JOBS + KV       # creates the stream
               KV       read/write

worker-internal
               sub      ada.viewer.jobs.convert.>
               JS API   $JS.API.CONSUMER.{CREATE,MSG.NEXT}.ADA_VIEWER_JOBS.>
               KV       read/write

worker-ext-01                                               # one per external host
               sub      ada.viewer.jobs.convert.cad         # its pool, nothing else
               JS API   $JS.API.CONSUMER.*.ADA_VIEWER_JOBS.ada-viewer-worker-cad
               KV       write $KV.ada-viewer-jobs.__meta_worker__ext-01
                        write $KV.ada-viewer-jobs.*          # job status — see residual risks
                        read  $KV.ada-viewer-jobs.>
```

The subject layout makes this practical without inventing anything: pools are
already `<subject>.<capability>` and durables are already
`<durable>-<capability>` (`queue.py: pull_subscribe`), so both the data subject
and the JetStream API subject can be pinned per capability as they stand.

### Identity comes from the credential, not from `$HOSTNAME`

`worker_id` is currently `os.environ["HOSTNAME"]` — self-asserted, and it is the
KV key the registry row is written under. Pin that key in the credential's
publish permission (`__meta_worker__ext-01` above) and the id becomes
**authenticated by NATS with no adapy code at all**: a worker cannot write a row
under a name it was not issued. This is worth doing even before admission
(§3), because it is what makes an admission list keyed by worker id meaningful.

### Code changes

* ✅ `QueueConfig` gains `creds_file`, `user`, `password`, `token`,
  `nkey_seed_file`, `tls_ca` (+ `ADA_VIEWER_NATS_*` env), and `connect()` passes
  them through to `nats.connect()`. `nats-py` supports all of these directly.
* ✅ **`connect(manage=False)` for workers.** Stream and consumer
  *administration* moves behind a flag the API sets and the worker does not, so
  worker credentials need no stream-admin rights. Without this, least privilege
  was impossible: the worker's first act was `add_stream`.
* Helm: optional `nats.auth` block writing a server config with the users above,
  a Secret per worker deployment, and `ADA_VIEWER_NATS_CREDS` wired into the
  worker/API pods. `nats.enabled=false` deployments pass their own URL and creds
  through unchanged.

All of it is backwards compatible: no creds configured => `nats.connect(url)`,
which is exactly today's behaviour.

## 3. Design B — admission: what the API will believe

Authentication says *who is talking*. Admission says *what the API accepts from
them*, and it is the control that stops plugin-id hijack.

Add an admin-owned allowlist — an `app_settings` key, editable from the existing
admin panel:

```json
{
  "ext-01":          { "capabilities": ["cad"],  "plugins": ["cad-export"] },
  "adapy-worker-*":  { "capabilities": ["*"],    "plugins": ["*"] },
  "*":               { "capabilities": [],       "plugins": [] }
}
```

`_live_worker_specs()` (and the sibling unions for `conversions`,
`source_exts`, the procedural catalogs) filter each row's advertisements through
the entry matching that worker id. A worker outside the list still appears in
`GET /api/admin/workers` — so an admin can *see* an unknown worker and decide
about it — but contributes nothing to `/api/plugins`, nothing to the upload
picker, and cannot claim a plugin id that is not its own.

Defaults matter more than the mechanism here:

* **Absent setting => allow everything.** Existing deployments do not change
  behaviour on upgrade, and nobody's cluster breaks because a doc was not read.
* **Present setting => deny by default** for ids that match no entry. Once an
  operator opts in, an unknown worker is inert rather than partially trusted.
* The admin panel should show unadmitted workers prominently with an
  **Admit** button that writes the entry — the enrollment flow is the thing that
  makes this get used rather than turned off.

Pair this with the KV key pinning from §2 and the two halves compose cleanly:
**NATS decides which row you may write; admission decides what the API believes
from that row.**

## 4. Design C — capability qualification

Today the only guard against a worker serving work it should not is the blunt
env flag `ADA_WORKER_BASE_CONVERSIONS=false`, set by hand on each foreign pool.
Its own comment in `worker.py` records why it exists: an extra-capability pool
built from an independent adapy *"advertises the full base converter matrix, so
it wins base conversion jobs it has no business running — and when that image is
stale it produces outdated output (e.g. non-manifold meshes)."*

That is a correctness property defended by remembering to set an env var. Invert
it: **a worker advertises a capability only if it can show it is fit to serve
it.**

### Requirement documents

Per capability, a small document naming what that capability's output depends
on. Authored by whoever owns the capability, stored in `app_settings` (so it is
editable without a redeploy) and republished by the API into the KV meta
keyspace that already carries `worker_image_tag`:

```json
{
  "base":     { "requires": { "ada-py": ">=0.51.0", "occt": ">=7.8.1",
                              "ada-cpp": ">=0.3.0" },
                "build_match": { "occt": "novtk_*" } },
  "meshing":  { "requires": { "example-mesher": ">=1.4.0" } },
  "cad":      { "requires": { "example-cad-bridge": ">=1.2.0" } }
}
```

`build_match` is not decoration: adapy's own `pixi.toml` pins `occt` and
`pythonocc-core` to the `novtk_*` build variant and requires the two to agree, so
"right version, wrong build" is a real way to be unfit.

### The gate, in two places

**Worker side (the one that matters).** At registration and on each heartbeat,
the worker compares its own environment against the document and **drops failing
capabilities from the set it advertises *and subscribes to***. Filtering the UI
alone would be useless — an unfit worker that still holds a consumer keeps
winning jobs.

The input already exists: `_capture_worker_packages()` reads `conda-meta/*.json`
(name, version, build, channel) plus pip dists. It is currently called only when
a DB pool *and* an image tag are present; make it unconditional and cache it —
it cannot change within a process lifetime.

Two details about "currently" that matter more than they look:

* Its output goes to **Postgres**, so audit rows can link to a toolchain. It is
  **not** in the registry row — `register_worker` sends `image_tag`,
  `capabilities`, `plugin_specs`, `conversions`, `source_exts` and no packages.
  So the API cannot see a worker's dependencies at all today, and the API-side
  check below has nothing to check against until this moves.
* An off-cluster worker typically has **no** `DATABASE_URL` (there is no reason
  to expose Postgres to it), so `_capture_worker_packages()` is never called
  there. The machine whose dependencies are least under our control is the one
  currently reporting nothing about them — a box carrying a two-year-old
  `ada-py` is indistinguishable from a current one.

Making it unconditional and registry-borne is therefore the *first* step of this
design, not an implementation detail of it.

The registry row gains a `withheld` field so the reason is visible instead of the
capability merely being absent:

```json
"withheld": [{ "capability": "base",
               "reason": "ada-py 0.44.1 does not satisfy >=0.51.0" }]
```

A capability silently missing is a support ticket; a capability that says why it
withheld itself is a fixed deployment.

#### Withheld is not absent, and the API must not conflate them

Writing the reason into the registry row is necessary and **not sufficient** —
an earlier draft of this document stopped there. The consuming endpoints
(`/api/plugins` and the sibling unions) list only what live workers *advertise*.
A worker that withholds a capability therefore vanishes from them entirely, and
every consumer sees precisely what it would see if the machine were switched off.

That is worse than unhelpful when the pool has one member. A viewer dialog
reading the plugin list says:

> no worker providing this capability is online — start one

which is false: one *is* running, it is simply unfit. The operator's next action
is to start a second, which will be unfit for the same reason, and nothing on the
path from symptom to cause mentions a version.

So the union endpoints must carry withheld capabilities as **present but
unavailable, with the reason**, rather than omitting them:

```json
{ "slug": "…", "online": true, "available": false,
  "unavailable_reason": "ada-py 0.44.1 does not satisfy >=0.51.0" }
```

The rule worth holding onto: **anything that can disable a capability must also
be able to say so to whoever is waiting for it.** A gate whose only externally
visible effect is an absence has re-created, one level up, the failure it was
built to prevent — work queued against a pool that will never serve it, looking
like slowness rather than misconfiguration.

#### The asymmetry this creates

Withholding is not equally cheap across capabilities, and the design should not
pretend otherwise:

* For **`base`**, withholding is strictly better than serving. A stale base
  worker produces *silently wrong output* — `worker.py`'s own comment cites
  non-manifold meshes — and other pool members remain to take the work.
* For a **capability with a single provider** (a licensed host, a machine with a
  device attached), withholding removes that capability from the deployment
  outright. Still the right call against wrong output written under a permanent
  key, but the whole justification rests on the failure being legible — which is
  why the paragraph above is a requirement and not a nicety.

**API side (defence in depth).** The union endpoints apply the same check
against the packages the worker reported. This covers the worker running code
too old to know about qualification — it cannot gate itself, so something must
gate it — and the worker that simply lies, which is the same trust question as
§3 and wants the same answer.

### Fail-open or fail-closed

Asymmetric, deliberately:

* **No requirement entry for a capability => advertise it.** Backwards
  compatible, and self-governing plugin capabilities need no central entry.
* **Entry exists but a required package is missing or unparseable => withhold**,
  with the reason. Silence about `cad` is fine; silence about `base` is not.

Conda versions are not reliably PEP 440 (epochs, build strings), so compare with
`packaging.version` where it parses and fall back to exact-string equality where
it does not — never to "probably newer".

### Beyond versions: an optional probe

Version comparison answers *is my code current*. It does not answer *does my
environment actually work* — and for a licence-holding worker that is the more
common failure: a machine whose licence server is unreachable must not advertise
its capability at
all, whatever its package versions say.

So let a capability register an optional `self_check() -> None` (raise to fail)
run at boot and on a slow cadence, in the same registry as the requirements. For
a CAD bridge it is a ping at the licence server. Version gate first; the probe hook is
a small addition on the same seam.

### What happens to `ADA_WORKER_BASE_CONVERSIONS`

It stays, as a manual override — "I *can* serve base but I do not want to" is a
legitimate operator choice that no automatic check should override. It stops
being the mechanism correctness depends on.

## 5. Rollout order

Each step is independently shippable and backwards compatible.

1. **`connect(manage=False)` for workers.** ✅ **Landed.** No behaviour change;
   unlocks least privilege later.
2. **Credentials plumbed through `QueueConfig`.** ✅ **Landed** (Python side;
   see the chart gap below). 0.54.0 completed it: the optional `aiohttp` and
   `nkeys` packages nats-py imports lazily, and the inline seed form
   (`ADA_VIEWER_NATS_NKEY_SEED_VALUE`) that a secret store populating the
   environment — rather than mounting a file — requires. Still optional;
   nothing changes until a deployment sets them.
3. **Turn auth on in the cluster**, all pools, api included. Now network
   position grants nothing.
4. **Capability qualification** (§4) ✅ **Landed.** Shipped as the three parts
   below, which had to go together:
   package data into the registry row (it goes only to Postgres today, and not
   at all on a worker without one), the worker-side gate with `withheld`, and
   the API surfacing withheld capabilities as *unavailable-with-a-reason*
   rather than absent. Shipping the gate without the last part trades a silent
   wrong answer for a silent missing one. Pays for itself immediately on the
   *existing* extra-worker pools, external workers or not.

   Two things the implementation settled that the design had left vague:

   * **The comparator carries no `packaging` dependency.** It is absent from the
     `viewer-api` environment, and a gate that needed it would have had to fail
     open on a hand-assembled machine — the one it is most for. Comparison is
     self-contained, orders dotted numeric releases, and *refuses* anything else
     rather than guessing at `1.0.0rc1` vs `1.0.0`.
   * **The API-side re-check is weaker than this document implied.** It was
     justified as covering a worker too old to gate itself, but such a worker
     also reports no packages, so there is nothing to re-check. Against a worker
     that *lies*, the answer is admission (§3), not this. What the API does do
     is surface the worker's own verdict, which is the part that matters.

   Requirements reach workers through the KV meta keyspace rather than the
   database, because the worker this is most for has no database connection.
   Evaluated once at startup and used for both advertisement and subscription,
   so the two cannot disagree; changing a requirement takes effect on restart.
   Taking a pool out of service *now* is a different job with a different tool.
5. **Admission list** (§3), default-allow until an operator opts in, with the
   Admit button in the admin panel.
6. **Only then** issue an external worker its credentials and admit it.

Steps 1, 4 and 5 are worth doing even if the external worker never ships.

### What landed in steps 1–2

| | |
| --- | --- |
| `QueueConfig` | `creds_file`, `user`, `password`, `token`, `nkey_seed_file`, `tls_ca` — all defaulting to empty |
| Env | `ADA_VIEWER_NATS_{CREDS,USER,PASSWORD,TOKEN,NKEY_SEED,TLS_CA}` |
| `JobQueue.connect` | `connect(manage: bool = True, name: str | None = None)` |
| API | `connect(manage=True, name="adapy-viewer-api")` — creates the stream and bucket, as before |
| Worker | `connect(manage=False, name=f"adapy-worker-{worker_id}")` — creates nothing |
| Escape hatch | `ADA_WORKER_MANAGE_STREAM=true` restores worker self-provisioning |

Two details worth knowing before step 3:

* **`manage=False` waits rather than provisions.** A worker that starts before
  the API polls for the KV bucket for 60s and then fails with a message naming
  the cause. Waiting on the bucket also covers the stream, because the managing
  path creates the stream first and the bucket last — a coupling noted in the
  code, since reordering it would produce a worker bound to a bucket with no
  stream to subscribe on.
* **The chart can already inject creds into workers but not into the API.**
  Worker pools have `extraEnv` / `extraEnvFrom` (`_helpers.tpl`), so a Secret
  reaches them with no chart change. `deployment.yaml` has no such hook, so
  step 3 has to add one (or an explicit `nats.auth` block) before the API can
  authenticate. Nothing else blocks turning auth on.
* **Connections are now named.** `nats server report connections` shows
  `adapy-viewer-api` and `adapy-worker-<id>` instead of `ip:port`, which is what
  makes "which principal is this" answerable while rolling auth out.

## 6. Residual risks

* **Job-status KV cannot be scoped per worker.** Job ids are random, so a
  credential able to update the job it is processing can update any job's status
  (not read its blobs). Accepted for now. The clean fix is to move status
  writes behind the API — worker → HTTPS with its own bearer — which is a
  larger change and worth revisiting if the external fleet grows.
* **Object store credentials on unmanaged machines.** Mint per-worker keys
  scoped to the bucket, and to the one key prefix that worker writes where the
  store supports prefix policies. The stronger answer is a REST-mediated storage kind
  so an external worker holds a viewer bearer token instead of bucket keys —
  the API already mints long-lived CLI tokens (`AuthConfig.cli_token_secret`),
  so the identity infrastructure exists; the storage facade does not.
* **A worker is still trusted with the data of the jobs it legitimately
  receives.** Admission bounds *which* jobs those are; it cannot make the
  content safe. Deciding which scopes an external host may see work from is a
  policy question, not a mechanism one — and worth answering before the first
  external worker is admitted to anything but its own capability.
