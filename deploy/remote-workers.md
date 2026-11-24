# Remote workers: job grants instead of storage keys

A **remote worker** runs outside the cluster: a workstation or VM that serves a capability pool
because it holds something no cluster node does (a GPU, a licence, a device). It needs the job
queue and the files of the jobs it runs, and nothing more. Before job grants it had to be given
bucket-wide object-store keys, which is the last residual risk in [`worker-trust.md`](worker-trust.md).

With job grants a remote worker holds **two things**: the API's URL and its own NATS key. For each
job it picks up it asks the API for a short-lived **job grant** over NATS and reads and writes that
job's files through the API's job-scoped storage routes. Large files still move between the worker
and the object store directly, through presigned URLs the API hands out per call.

Everything here is opt-in. Workers with `ADA_VIEWER_S3_*` settings, in or out of the cluster,
behave exactly as before.

Today job grants cover **simulation runs**. Plugin jobs are refused, for the reason given
[below](#why-plugin-jobs-are-not-granted-yet).

## How a job is granted

```
worker                                   API (grant responder, queue group)         object store
  | pull job J (pool sim-x)                 |                                           |
  | NATS request  ada.viewer.grants.sim-x.ws-01  {"job_id": J}                          |
  |  (reply on _INBOX_ws-01.*)  ----------> | job J exists, not ended                   |
  |                                         | J is a simulation run; its capability is  |
  |                                         |   sim-<provider id>, COMPUTED (not taken  |
  |                                         |   from the worker registry) == sim-x      |
  |                                         | bind J -> ws-01 (a second worker: refused)|
  |  <------------- {"ok": true, "token": <job grant>, "ttl_s": 900, ...}               |
  |                                         |                                           |
  | GET /api/jobs/J/storage/object?key=_simulations/<run>/run.json  (Bearer grant)      |
  | POST /api/jobs/J/storage/upload {key, size} ---> | presign / start multipart --->   |
  | PUT <presigned URL> ------------------------------------------------------------->  |
  | ... at 2/3 of the grant's life: the same NATS request again, a fresh grant          |
```

1. The worker connects to NATS with a **private inbox prefix**, `_INBOX_<worker name>`
   (`ADA_VIEWER_NATS_INBOX_PREFIX`; a worker in grants mode sets it itself). Its key may subscribe
   to `_INBOX_<worker name>.>` and nothing wider, so no other client reads its replies, and keys
   that subscribe to `_INBOX.>` do not match `_INBOX_<worker name>.*` either: a NATS wildcard
   matches whole tokens.
2. It sends `{"job_id": "..."}` on `<prefix>.<capability>.<worker name>` (prefix
   `ada.viewer.grants`). Its key may publish only its own capability and name there, so **the
   subject itself is authenticated**: NATS checked it, no adapy code has to.
3. The API's responder (a queue group on the API's own connection: one answer per request, however
   many API replicas) checks, in order: the job exists and has not ended; it is a kind that is
   granted; the subject's capability is the job's, computed from the job (`sim-<provider id>`);
   and binds the job to the worker name, first come first bound. A second worker asking for a
   bound job is refused until the holder has not asked for a whole grant lifetime (it stopped,
   and the queue redelivered its job); then the second worker takes the job over, and the first
   one's grant no longer matches the binding. A redelivered job's worker waits for that rather than
   dropping the job. The binding lives in a KV bucket of the API's own (`ada-viewer-job-grants`)
   that worker keys have no permission on.
4. The reply is the grant: an HS256 JWT signed with `ADA_VIEWER_WORKER_GRANT_SECRET`, with its own
   issuer (`ada-viewer-worker-grant`) and the claims below. The user-facing auth refuses it, and the
   storage routes refuse everything else.
5. The worker renews the grant at two thirds of its life with the same request. A job that has
   ended gets no renewal. Removing the worker's key from `nats.conf` revokes it within one grant
   lifetime (15 minutes by default).

`{"job_id": "probe"}` always answers `no_such_job`, without looking anything up. A worker's
preflight uses it to prove that its key may publish the grant subject and that the reply reaches
its private inbox.

### The token

| claim    | value                                                        |
| -------- | ------------------------------------------------------------ |
| `iss`    | `ada-viewer-worker-grant`                                    |
| `job_id` | the job                                                      |
| `scope`  | the job's storage scope, as its prefix (`projects/<id>`, ...) |
| `cap`    | the capability the grant was asked for (`sim-<provider id>`) |
| `worker` | the worker name from the subject                             |
| `iat`, `exp` | issued at, and `iat` + `ADA_VIEWER_WORKER_GRANT_TTL_S` (900 s) |

The token says **which job and which worker**. It does not say what may be read or written:
every route call works that out again from the job and its run record, so a grant for one job is
useless on another's routes, and a run whose record gains an input can read it at once.

### What a grant allows

| | |
| --- | --- |
| **read** | the run record (`_simulations/<run>/run.json`), the run's `inputs`, its cancel marker, everything under its own directory, and the provider's declared read prefixes; provider data is resolved through the scope's provider grants, as the blob routes resolve it |
| **list** | under a read prefix or the run's own directory |
| **write** | `_simulations/<run>/` and nothing else |

A provider declares the extra prefixes its runs read (shared assets, for example) in code that
the **API** imports:

```python
from ada.simulation import declare_storage_read_prefixes

declare_storage_read_prefixes("my-provider", ["assets/robots/"])
```

or as a `storage_read_prefixes` attribute of the provider object, or per deployment with
`ADA_VIEWER_WORKER_GRANT_READ_PREFIXES='{"my-provider": ["assets/robots/"]}'`. A prefix under an
internal namespace (`_simulations/`, `_derived/`, ...) or the whole scope is ignored.

## The routes

All under `/api/jobs/{job_id}/storage/`, with `Authorization: Bearer <job grant>` and nothing else.
They are mounted outside the user-auth router, and answer 404 while job grants are off.

| route | what |
| --- | --- |
| `GET object?key=` | a small object's bytes (gzip at rest undone); 413 above `small_object_max` |
| `PUT object?key=[&content_encoding=gzip]` | a small object (a run record, a log) |
| `GET download-url?key=` | `{url, via, size, expires_in}`: a presigned GET on the store's public endpoint (`via: "store"`), or this API's `blob` route for a store that cannot presign (`via: "api"`) |
| `GET blob?key=` | the stored bytes through the API |
| `GET list?prefix=` | `{keys: [{key, size}]}` |
| `POST upload` `{key, size}` | up to the part size: `{mode: "single", url}`, one presigned PUT. Above it: `{mode: "multipart", upload_id, part_size, part_count, parts: [{part_number, url}]}` with the first 64 part URLs |
| `POST upload/parts` `{key, upload_id, part_numbers?}` | `{uploaded: [{part_number, etag, size}], parts: [...]}`: what the store already holds (ListParts, to resume) and fresh URLs |
| `POST upload/complete` `{key, upload_id, parts?}` | checked against what the store holds: every part present, with that ETag, numbered 1..n, and the total within `max_size` |
| `POST upload/abort` `{key, upload_id}` | |

The multipart upload is the store's own: the API makes CreateMultipartUpload, ListParts,
CompleteMultipartUpload and AbortMultipartUpload itself, and hands out presigned UploadPart URLs,
all signed by re-signing an obstore URL with the `uploadId` / `partNumber` query and the method
(`ada/comms/rest/presign.py`). A store whose secret the API does not hold, or a filesystem store,
gets **staged** parts instead: each part is PUT to the API, kept under `_derived/_grant_uploads/`
and joined on completion. That path carries the bytes through the API and exists for dev stacks.

Grants, writes, uploads and presigned downloads leave audit rows with `user_sub = worker:<name>`.

## Settings

### The API

| setting | default | |
| --- | --- | --- |
| `ADA_VIEWER_WORKER_GRANT_SECRET` | (unset: off) | the HMAC key grants are signed with; 32 bytes or more (`openssl rand -hex 32`) |
| `ADA_VIEWER_WORKER_GRANT_TTL_S` | `900` | grant lifetime |
| `ADA_VIEWER_WORKER_GRANT_SUBJECT_PREFIX` | `ada.viewer.grants` | |
| `ADA_VIEWER_WORKER_GRANT_KV_BUCKET` | `ada-viewer-job-grants` | the bindings bucket (the API creates it) |
| `ADA_VIEWER_WORKER_GRANT_PART_SIZE` | 64 MiB | single PUT up to this; multipart above (at least 5 MiB) |
| `ADA_VIEWER_WORKER_GRANT_MAX_SIZE` | 200 GiB | largest object one grant uploads |
| `ADA_VIEWER_WORKER_GRANT_SMALL_OBJECT_MAX` | 8 MiB | largest body `object` carries |
| `ADA_VIEWER_WORKER_GRANT_READ_PREFIXES` | `{}` | JSON, provider id -> extra read prefixes |
| `ADA_VIEWER_NATS_PUBLIC_URL` | (unset) | the NATS URL remote workers are told to use (`wss://...`) |
| `ADA_VIEWER_WORKER_MIN_ADA_PY` | (unset) | the oldest ada-py a remote worker should run |
| `ADA_VIEWER_WORKER_BOOTSTRAP_EXTRA` | `{}` | JSON object, capability -> the deployment's own settings for it; served as is (or the `worker_bootstrap_extra` app setting) |

The responder needs the queue: with a secret and no `ADA_VIEWER_NATS_URL` it logs that it is not
answering. The API's own NATS principal needs to subscribe `ada.viewer.grants.*.*` and create and
use the bindings bucket.

### The worker

| setting | |
| --- | --- |
| `ADA_VIEWER_API_URL` | the host URL |
| `ADA_VIEWER_WORKER_NAME` | its name: one subject token (`[A-Za-z0-9_-]`); defaults to `HOSTNAME` |
| `ADA_VIEWER_NATS_URL` | from the bootstrap's `nats.url` |
| `ADA_VIEWER_NATS_NKEY_SEED` (file) or `..._VALUE` | its key |
| `ADA_VIEWER_NATS_INBOX_PREFIX` | `_INBOX_<worker name>`; set automatically in grants mode |
| `ADA_VIEWER_STORAGE_KIND=grants` | or leave it unset: a worker with `ADA_VIEWER_API_URL` and no `ADA_VIEWER_S3_BUCKET` / `ADA_VIEWER_LOCAL_PATH` picks `grants` itself. The API never does. |

No `ADA_VIEWER_S3_*`, no `DATABASE_URL`, no `ADA_VIEWER_TOKEN`.

## `GET /api/workers/bootstrap`

Public and non-secret. What a worker setup reads from the host URL:

```json
{
  "schema": "ada.viewer/worker-bootstrap@1",
  "api_version": "0.172.1",
  "nats": {"url": "wss://bus.example"},
  "grants": {
    "enabled": true,
    "subject_prefix": "ada.viewer.grants",
    "subject": "ada.viewer.grants.<capability>.<worker name>",
    "inbox_prefix": "_INBOX_<worker name>",
    "ttl_s": 900, "refresh_after_s": 600, "probe_job_id": "probe",
    "storage_path": "/api/jobs/<job id>/storage",
    "small_object_max": 8388608,
    "multipart": {"part_size": 67108864, "max_size": 214748364800}
  },
  "worker": {"min_ada_py": "0.172.0"},
  "capabilities": {"sim-x": {"...": "the deployment's own block"}}
}
```

## The remote worker kit (`ada.comms.rest.remote_worker`)

| | |
| --- | --- |
| `fetch_bootstrap(url)` | the document above, parsed |
| `GrantClient(request, subject_prefix=, capability=, worker_name=, job_id=)` | asks, caches, renews at 2/3 |
| `RemoteGrantStorage(job_id, token_provider, api_url=)` | `list_keys`, `get_bytes`, `fetch_to_path`, `put_bytes`, `put_path`, `provider_denial`: the same six methods as the worker's `_SyncStorageFacade`, so `execute_run` and providers are unchanged. Multipart with per-part retry, and `upload_file(..., resume_upload_id=)` |
| `GrantModeStorage` | what the adapy worker uses in grants mode: `open_job(...)` asks for the grant and binds a facade; any other storage call fails saying the worker holds no keys |
| `active_grant_storage(run_id)` | the facade of a run while it runs here, for provider code that reads a declared prefix |
| `check_seed(seed, public_key)` | does the seed belong to the key `nats.conf` names? |
| `check_version_floor(installed, floor)` | against the bootstrap's `min_ada_py` |
| `classify_nats_error(err)` | an **authorization** violation (the server does not know the key) or a **permissions** violation (a subject the entry lacks) |
| `probe_grant(request, ...)` | the `probe` request; "no such job" is the pass |
| `nats_conf_entry(...)` / `render_nats_conf_entry(entry)` | the server-side entry for the key |

None of these puts a seed in a message, a log line or a `repr`.

### The `nats.conf` entry

`nats_conf_entry(public_key=..., worker_name="ws-01", capabilities=["sim-x"])` renders:

```
{
  nkey: "U..."
  permissions: {
    publish: {
      allow: [
        "ada.viewer.grants.sim-x.ws-01"
        "$JS.API.CONSUMER.INFO.ADA_VIEWER_JOBS.ada-viewer-worker-sim-x"
        "$JS.API.CONSUMER.CREATE.ADA_VIEWER_JOBS.ada-viewer-worker-sim-x"
        "$JS.API.CONSUMER.CREATE.ADA_VIEWER_JOBS.ada-viewer-worker-sim-x.>"
        "$JS.API.CONSUMER.DURABLE.CREATE.ADA_VIEWER_JOBS.ada-viewer-worker-sim-x"
        "$JS.API.CONSUMER.MSG.NEXT.ADA_VIEWER_JOBS.ada-viewer-worker-sim-x"
        "$JS.ACK.ADA_VIEWER_JOBS.ada-viewer-worker-sim-x.>"
        "$JS.API.STREAM.INFO.KV_ada-viewer-jobs"
        "$JS.API.DIRECT.GET.KV_ada-viewer-jobs.>"
        "$JS.API.STREAM.MSG.GET.KV_ada-viewer-jobs"
        "$KV.ada-viewer-jobs.*"
      ]
    }
    subscribe: {
      allow: [
        "_INBOX_ws-01.>"
        "ada.viewer.jobs.convert.sim-x"
      ]
    }
  }
}
```

With `registry_bucket=` set (the registry split, `ADA_VIEWER_NATS_REGISTRY_KV_BUCKET`) it also
allows the worker's own registry row and nothing else in that bucket.

## Why plugin jobs are not granted yet

A simulation run's capability is computed from its provider id, which the API wrote into the job.
A plugin job's capability is whatever the worker registry says the plugin's pool is, and while the
registry shares the jobs bucket, any worker key that can report job progress can write any
registry row ([`worker-trust.md`](worker-trust.md) §2). Granting by that capability would let a
key that serves one pool claim another plugin's jobs, and write that plugin's outputs. Plugin jobs
are refused with `kind_not_granted` and that reason until either:

* the registry has a bucket of its own (`ADA_VIEWER_NATS_REGISTRY_KV_BUCKET`) and each worker key
  is pinned to its own row, so a registry row's claim is authenticated; or
* an admin allowlist maps capabilities to plugin ids, so the API need not believe a row at all.

Either gives the plugin job a capability the API can trust; the write prefix is then the plugin
job's `derived_prefix`, behind the same check.

## What is still trusted

* **A grant outlives its job by at most one lifetime.** The routes do not refuse a job that has
  ended: the cancel route marks the job cancelled before its worker writes the record that says
  so, and that last write is what the user is waiting for. An ended job gets no renewal.
* **A presigned single PUT is not size-checked by the store**: the API checks the declared size,
  a multipart upload's total is checked at completion, but a single PUT URL accepts what it is
  sent until it expires.
* **Binding is first come, first bound** within a capability. Two workers of one pool racing for
  a job get one binding between them; a key of another pool cannot ask for that job at all. A
  holder that is alive but makes no storage call for a whole grant lifetime loses its binding if
  the queue redelivers its job meanwhile (which it does only when the holder stopped renewing its
  lease).
* **Job status in KV** remains writable by any worker key (`worker-trust.md` §6).
