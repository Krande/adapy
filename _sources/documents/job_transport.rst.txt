Job transport
=============

The REST API has two ways to run a job, and one contract over them:
``ada.comms.rest.job_transport.JobTransport``. It is built once, at app build
time, from whether a queue URL is configured, and carried on
``RestContext.jobs`` — so no request ever asks which shape the deployment is.

``QueueJobTransport``
    A NATS-backed worker pool. Every job kind is available. Jobs are enqueued
    onto the pool advertising the needed capability and their progress lives in
    the queue's KV bucket.

``LocalJobTransport``
    No queue. **Plugin jobs run in this process**, in a thread
    (``ada.comms.rest.local_jobs``); every other job kind is unavailable.

Why the asymmetry is the contract
---------------------------------

The right shape for a deployment is the wrong shape for one person running the
viewer on a laptop. A plugin's backend job is long and CPU-heavy and wants its
own pod — but with no NATS, ``POST /api/plugins/{id}/jobs`` used to answer 503
and the plugin's "run" button was dead in exactly the setup the examples put
you in. So a plugin job runs locally instead, through the *same*
``submit(JobRequest)`` the queue path uses: same entrypoint, same synchronous
storage facade, same ``on_progress`` and ``cancel_event``, and a job id that
``GET /api/convert/{job_id}`` serves. The plugin cannot tell the difference.

Nothing else can run locally, and that is a statement rather than an omission:
a conversion needs a worker's CAD stack, a procedural build needs its engine's
pool. ``LocalJobTransport`` reports those as unavailable so the refusal is one
fact in one place instead of a branch each route remembers or forgets.

The contract
------------

Gating — one call, and one 503 text per feature:

``supports(feature) -> bool``
    Whether this transport can run that kind of work.
``unavailable(feature)``
    Raise the 503 for a feature it cannot. The detail strings live in
    ``FEATURE_UNAVAILABLE_DETAIL`` and are API surface: clients and tests read
    them, so they are pinned rather than generated from the feature name.
``require(feature)``
    ``supports`` or ``unavailable``.

Jobs:

``submit(req, *, before_dispatch=None) -> SubmittedJob``
    Run or enqueue a ``JobRequest``; raises the feature's 503 if unsupported,
    so a route that forgets the gate still refuses correctly.
    ``before_dispatch`` is awaited between the job becoming *durable* and
    becoming *visible* to anything that would run it — the window in which a
    caller writes its audit row, because the worker's own audit writes are
    bare ``UPDATE ... WHERE job_id`` and a fast job can outrun the API's
    INSERT. A transport with no dispatch step does not call it.
``inprocess(job_id)``
    The in-process job with this id, or ``None``. Synchronous and free; the
    queue transport always answers ``None`` without touching the network.
``status(job_id)`` / ``cancel(job_id)``
    The job's current state, and a request to stop it. In-process cancellation
    is real (the entrypoint holds the same ``cancel_event``); queued
    cancellation nudges the KV record and the worker runs to completion.

Capability reporting:

``capabilities()``, ``advertised_specs(field, fallback_field=None)``,
``local_specs()``, ``worker_image_tag()``
    What a route may truthfully say is available. Without a pool the first two
    are empty and ``local_specs()`` returns what *this* process registered —
    online by definition, being the thing that would run the job.

Queue-only features
-------------------

Everything except ``plugin_jobs``:

``bake``, ``bbox_inference``, ``component_build``, ``conversion``,
``job_status_report``, ``procedural_build``, ``procedural_export``,
``procedural_import``, ``procedural_relocations``, ``result_meta``,
``utilities``, ``worker_registry``.

Not a transport concern
-----------------------

Things that need a queue as *storage* rather than as a way to run work stay on
``RestContext.queue``: the KV meta keyspace, the compression-sweep state, the
completed-job purges, and the worker-registry refresh loop that fills the
snapshot the transport reads. Folding those in would make this the place where
"is NATS configured" is asked, rather than the place where "can this job run"
is answered.
