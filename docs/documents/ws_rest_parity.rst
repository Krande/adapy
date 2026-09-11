Websocket and REST as peers: local disk as a storage backend
==============================================================

.. note::
   A **plan**, not a description of what exists. Nothing in this document is implemented. It also
   has a **prerequisite that is not on main yet**: the ``ProceduralModelCapability`` seam it builds
   on (including ``canEdit``) lives on the ``fix/viewer-procedural-panels`` branch, not here --
   ``src/frontend/src/services/capabilities/`` on main carries only ``stats``. Every citation below
   marked *(branch)* refers to that branch; unmarked citations are against main. Delete this
   document, or fold what survives into the comms docs, once the work lands.

The viewer has two transports. Over REST it talks to a FastAPI app with Postgres, object storage,
NATS and a worker pool behind it. Over websocket it talks to a Python process on the user's own
machine. Today the first is treated as the real one and the second as a degraded preview of it, and
that asymmetry is written into the code as though it were a property of the transports rather than
a description of what someone has got round to implementing.

**It is the second thing.** The websocket path has a live Python process on the other end -- that
process is what built the scene and pushed the GLB down the socket in the first place. It has
``ada`` imported. It can read and write the user's disk. When the frontend says the local viewer's
procedural panels are read-only "because there is no backend to commit to", the honest statement is
*the commit verb has not been implemented over this transport yet*. The backend is right there.

The goal this document plans toward: **websocket and REST as peers behind one capability
interface**, with the local filesystem as a storage backend reached through the websocket layer for
as long as the connection is up. Not REST-with-bits-missing. A second implementation of the same
contract, whose storage happens to be a directory instead of a bucket.

Step 0 -- what is landing separately, and what this builds on
---------------------------------------------------------------

.. note::
   **In progress on** ``fix/viewer-procedural-panels``, **not part of this plan.** Described here
   because everything below assumes it, and because whoever picks this up should not redo it.

The capability seam already declares the flag this whole plan turns on. ``ProceduralModelCapability``
*(branch)* carries ``fetchModel``, ``adoptEmbeddedModel`` and::

    /** Whether the model this transport serves can be edited and committed back. False on the
     * websocket path -- the document came out of a GLB and there is no backend to commit to, so the
     * panels are read-only there. */
    readonly canEdit: boolean;

``WSProceduralModelCapability.canEdit = false``, ``RESTProceduralModelCapability.canEdit = true``,
both covered by ``__tests__/services/proceduralCapability.test.ts`` -- and **no production code
reads it**. Step 0 wires it into ``CellBuilderPanel`` and ``Menu`` so the local viewer's
equipment/system browser appears, read-only, instead of not appearing at all.

Two things about step 0 matter to this plan:

*It gates on a capability, not on a session.* The panel is currently hidden by
``proceduralActive = useCellBuilderStore((s) => s.active !== null)`` (``Menu.tsx:155``), where
``active`` is set only by ``open()`` (``cellBuilderStore.ts:1569``) and never by ``loadFromDoc()``
(``:2799``) -- the embedded/GLB path. Faking an ``active`` to reveal the panel would also switch on
the entire editing surface, because ``active`` additionally gates the context menu, port menu,
insert menu and gizmo HUD (``Menu.tsx:373-378``) and roughly twenty pointer/gizmo handlers in
``CellBuilderController.tsx``. Gating on ``canEdit`` instead keeps "is there a session" and "can
this transport write" as separate questions, which is the precondition for flipping the second one
later without touching the first.

*The docstring above should be reworded as part of step 0.* "There is no backend to commit to" is a
permanent-sounding concession to a temporary fact, and this plan exists to make it false. "The
commit verb is not implemented over this transport yet" says the same thing about today without
claiming anything about tomorrow.

**What step 0 must not do.** It must not introduce a store-local ``embedded``/``readOnly`` boolean
parallel to ``canEdit``. A second flag meaning almost the same thing is a second thing to unwind
when the answer changes, and the seam already has the first one.

Three kinds of thing the panel needs
--------------------------------------

The cellbuilder panel makes roughly a dozen distinct backend calls. They are not one problem; they
are three, and conflating them is what makes "make WS a peer" sound larger than it is.

**(i) Pure in-process Python that already exists.** The five type catalogs the panel's dropdowns
are built from are plain importable functions. The REST worker does not compute them -- it calls
them and advertises the result (``src/ada/comms/rest/worker.py:5035-5075``):

* ``list_equipment_types()`` / ``equipment_archetype_specs()`` (``ada.topo_model.equipment``) --
  backs ``fetchEquipmentTypes``
* ``list_system_types()`` / ``system_type_specs()`` (``ada.api.systems``, see
  ``src/ada/api/systems/base.py``) -- backs ``fetchSystemTypes``
* ``design_ruleset_specs()`` (``ada.topo_model``) -- backs ``fetchDesignRulesets``
* ``procedural_cell_type_specs()`` / ``procedural_opening_type_specs()`` -- back ``fetchCellTypes``
  and ``fetchOpeningTypes``

A local ``ada`` process serves all five by calling the same functions. There is no new computation
to write, and no server semantics to reproduce: REST's contribution is *unioning these with a
per-scope database catalog*, and locally that union has one side. ``fetchEngines``,
``fetchBlueprints`` and ``fetchDetailingEngines`` have the same shape -- an in-process registry,
plus a DB half (``rest/db.py`` ``list_procedural_engines``) that locally is empty.

Compile belongs here too: it is ``ada.topo_model.compile``, in process. Note that the browser
*already* compiles without any server, through Pyodide (``compileInBrowser``,
``cellBuilderStore.ts:3337``) -- so the local viewer can produce a result today. Compiling over the
websocket would be strictly better than that (real CPython, real CAD kernel), but it is an
improvement to something that works, not a gap.

**(ii) Genuinely new work: local-disk storage semantics.** Save, load, list and rename a procedural
model as a file. This is the only category that requires designing something that does not exist in
any form today, and it is the category that makes the panel honestly editable.

**(iii) Cloud-only, and should stay REST-only forever.** Multi-user scoping, authentication and
permission checks, the NATS job queue, worker pools. Two panel features belong here that might look
like category (i) at a glance:

* ``resyncEquipmentTypes`` -- it upserts the *per-scope database* catalog from code archetypes.
  Locally there is no catalog to resync into; the code archetypes already are the catalog. The
  button should be hidden on the websocket transport, not reimplemented.
* The embedded ``EquipmentAdminPanel`` / ``SystemAdminPanel`` subpanels -- per-scope DB catalog
  CRUD, same reasoning.

Getting this three-way split right is most of the value of this document. Category (i) is nearly
free, (iii) is a decision not to build, and only (ii) is engineering.

The precedent: this repo has already ratified the idea
--------------------------------------------------------

``src/ada/comms/rest/local_jobs.py:1-24`` faced exactly this tension for plugin jobs and resolved it
the way this plan proposes to resolve it for the cellbuilder::

    A plugin's on-demand backend job normally goes onto NATS and is picked up by a
    capability worker. That is the right shape for a deployment: the checks are long,
    CPU-heavy and want their own pods.

    It is the wrong shape for one person running the viewer on their laptop. There is
    no NATS, so ``/api/plugins/{id}/jobs`` answered 503 and the plugin's "run" button
    was dead -- in the exact setup the examples put you in [...]

    So: when no queue is configured, run the job HERE, in a thread, and report it
    through a job id the existing ``GET /api/convert/{job_id}`` endpoint can serve.
    The plugin sees the same contract either way -- the same ``job_entrypoint``, the
    same sync storage facade, the same ``on_progress`` and ``cancel_event`` -- so
    nothing about a plugin has to know which mode it is in.

That last sentence is the whole design, stated for a different subsystem. It also names its own
limits honestly ("Deliberately NOT a queue. One dict, one executor, no persistence, no retries"),
which is the standard this plan should be held to as well. What follows is that idea applied one
layer out: not a local implementation of a job queue, but a local implementation of *storage*.

The protocol: cheap per verb, with one structural blocker
-----------------------------------------------------------

**Adding a verb is cheap.** Three touchpoints: an enum value in
``src/flatbuffers/schemas/commands.fbs``, a handler module under ``src/ada/comms/msg_handling/``
(one file per verb, by convention), and one ``elif`` in ``default_on_message.py:31-56``, which is a
flat if/elif chain over ``message.command_type``. The current vocabulary::

    PING=0, PONG=1, UPDATE_SCENE=2, UPDATE_SERVER=3, MESH_INFO_CALLBACK=4,
    MESH_INFO_REPLY=5, LIST_WEB_CLIENTS=6, LIST_FILE_OBJECTS=7, LIST_PROCEDURES=8,
    RUN_PROCEDURE=9, ERROR=10, SERVER_REPLY=11, VIEW_FILE_OBJECT=12,
    DELETE_FILE_OBJECT=13, START_NEW_NODE_EDITOR=14, START_FILE_IN_LOCAL_APP=15,
    GET_SERVER_INFO=18, SHUTDOWN_SERVER=19

*(Values 16 and 17 are unused. Presumably retired verbs; nobody has checked, and it does not matter
except as a reminder not to reuse them casually.)*

**It is genuinely request/response, not one-way push.** ``ServerReply`` carries
``reply_to: commands.CommandType`` (``server.fbs:12-18``); handlers answer with
``CommandTypeDC.SERVER_REPLY`` and ``ServerReplyDC(reply_to=message.command_type)`` (see
``msg_handling/list_file_objects.py`` for the canonical shape); the frontend switches on it in
``utils/fb_handling/handle_incoming_buffers.ts`` (the ``SERVER_REPLY`` case).

**The blocker: there is no correlation ID.** ``table Message`` (``message.fbs:10-24``) carries
``instance_id``, ``target_id``, ``target_group``, ``client_type`` -- all of which route messages
*between clients* -- and no ``request_id``. Replies are dispatched by ``reply_to`` command-type into
a handler that writes to a global store. Two consequences, and they are the reason this is a
structural blocker rather than an inconvenience:

1. **You cannot await a specific response.** Every method the cellbuilder store calls on
   ``viewerApi`` is ``async`` and returns a value to its caller. There is no way to express that
   over a protocol where the answer arrives as a side effect on a store.
2. **You cannot have two of the same verb in flight.** Both replies route to the same handler by
   command type, and neither carries anything saying which request it answers.

Until a request ID exists, the two transports **cannot share the capability interface**, because the
interface is defined in terms of promises that resolve with values. Everything else in this plan is
per-verb work that can land incrementally; this one thing has to be done first, and once.

The fix is small in shape -- one field on ``Message``, a pending-promise map keyed by it on the
client, handlers echoing it back -- but it touches the schema, so it requires regenerating both
sides. **Unverified:** ``pixi run flat`` (``pixi.toml``) wraps ``src/flatbuffers/update_flatbuffers.py``
and appears to regenerate the Python and TypeScript bindings together; nobody has run it as part of
scoping this, so treat the codegen cost as unmeasured rather than known-cheap.

The second structural cost: the store bypasses the seam
---------------------------------------------------------

``cellBuilderStore.ts`` calls ``viewerApi`` **directly** throughout -- ``fetchEquipmentTypes``,
``commit`` (``:3200``), ``compilePreview``, all of it. It does not go through
``capabilities.procedural``. So "make the transports peers" implies a refactor the verb work does
not by itself deliver: routing the store's procedural calls through the capability seam.

This is larger than any single verb and should not be attempted as one change. Do it incrementally
-- one store method moved onto the seam per websocket verb that lands, so each move is justified by
a second implementation actually existing. A wholesale refactor ahead of the verbs would produce a
seam with one implementation on both sides of it, which is just indirection.

Migration: smallest useful first
----------------------------------

1. **Wire ``canEdit`` into the panel** (step 0, above, already in progress). Ships value on its own:
   equipment/system browsing works in the local viewer. Nothing to unwind later.
2. **Add ``request_id`` to ``Message`` and a pending-promise map to ``ws_comms.ts``.** Unblocks
   every verb below. Do it once, before any of them.
3. **``SAVE_PROCEDURAL_MODEL``.** Handler writes the document to a path and replies with whatever
   concurrency token the design settles on (see the traps). Flip
   ``WSProceduralModelCapability.canEdit`` to ``true``; revert step 0's guard on the Commit button.
4. **Type catalogs over the websocket.** One verb serving all five, or five thin ones. Pure function
   calls (category (i)); the panel's dropdowns stop being empty.
5. **``COMPILE_PROCEDURAL``.** In-process ``ada.topo_model.compile``, replying with a GLB the
   existing scene path already knows how to load.
6. **``LIST_PROCEDURAL_MODELS`` / ``LOAD_PROCEDURAL_MODEL``.** A local-disk model browser, so the
   storage panel works locally too.

**Why save is the first verb and not compile.** Compile is already possible with no server at all --
``compileInBrowser`` does it in Pyodide today. Implementing compile over the websocket first would
prove the plumbing while changing nothing a user cannot already do. Save is the one verb whose
absence is *why* the panel is read-only: land it and the panel becomes honestly editable, which is
the point of the exercise. It is also the verb that forces every hard question in the traps section
below to be answered, rather than deferred behind an easier one.

**Does the capability need new methods?** ``saveModel(doc): Promise<{...}>`` fits the existing grain
of ``ProceduralModelCapability`` *(branch)* and is the obvious shape. Note the seam is currently
read-oriented (``fetchModel``, ``adoptEmbeddedModel``, plus the ``canEdit`` flag), so save is the
first *write* it carries; if a second write verb follows quickly, it may be worth a separate
write-capability interface rather than growing the read one. That call should be made when the
second write verb exists, not now, on one data point.

Traps: where REST assumptions leak
------------------------------------

*Scope strings.* ``currentScopePart()`` (``cellBuilderStore.ts:244``) defaults to ``"user:me"`` and
is threaded into every procedural call. Locally there are no scopes and no users. The trap is that
passing ``"user:me"`` locally *works by accident* and then quietly means something the day a local
model is synced to a server. Model local as an explicit sentinel the websocket transport recognises
and the REST transport rejects -- not by reusing a real scope string that happens to be the default.

*Model id: opaque token vs file path.* REST treats ``modelId`` as an opaque URL-encoded token. A
path can be stuffed into that slot and it will appear to work, which is exactly why it should not
be: separators, drive letters and case-insensitive comparison on Windows all leak into a field every
consumer assumes is opaque. Give a local model a stable id and carry the path as its own field.

*Revisions and optimistic concurrency.* ``commit`` sends ``base_revision`` and handles HTTP 409
(``cellBuilderStore.ts:3200``); the panel renders ``r{revision}`` in its header and footer. A file
has no revision counter. mtime is the obvious substitute and is **weaker than it looks**: coarse
resolution, and it moves backwards on restore-from-backup, so as a concurrency token it can fail to
detect a real conflict. Either use a content hash, or drop the concept for local models (revision
``0``, no conflict path) and say so. Do not pretend mtime is a revision number. Note that the
panel's ``r{...}`` display is an unconditional dereference of ``active`` that step 0 has to make
optional anyway.

*Derived-key round-trips.* REST compile returns a blob key the frontend then fetches back. Locally
the bytes are already in the process that produced them; addressing them through a key-value store
to hand them to a viewer on the same machine is pure overhead. The websocket path should reply with
bytes -- it already does exactly that for scenes. The trap is implementing the derived-key contract
locally *for symmetry*, thereby inheriting a storage abstraction that exists to solve a problem the
local case does not have.

*Auth.* ``authedFetch`` wraps every REST call and ``runtime.authEnabled()`` is false on the desktop
path. Low risk today, but any code path shared between the transports must not assume a token
exists.

Definitions of done
---------------------

Written against observable behaviour rather than wiring, because "the capability reports it can
edit" is satisfiable without anything reaching disk.

* **Step 2 (correlation).** Two requests of the *same* verb issued back-to-back over the websocket
  both resolve, each with its own response, verified by a test that interleaves them deliberately
  rather than by two sequential calls that happen to pass.
* **Step 3 (save).** Edit a procedural model in the local viewer opened via ``assembly.show()``,
  save it, kill the process, reopen the file from disk, and see the edit -- with the Commit button
  enabled because ``canEdit`` is true, not because a guard was removed.
* **Step 4 (catalogs).** The Equipment/Systems dropdowns in the local viewer list the same types
  ``ada.api.systems.list_system_types()`` returns in a REPL in the same environment. Same source,
  so the comparison is exact, not approximate.
* **Step 5 (compile).** A model compiled over the websocket and the same model compiled through
  REST produce the same geometry for the same input document.

What this plan deliberately does not attempt
----------------------------------------------

* **Multi-user anything, locally.** No scopes, no permissions, no sharing. One person, one machine,
  one filesystem. A local backend that grows a user model has become a server, badly.
* **A local job queue.** Compile over the websocket runs in a thread and reports through the reply
  it already has. ``local_jobs.py`` is the precedent and it says the same thing about itself:
  anything wanting retries, persistence or cross-process coordination wants NATS.
* **Offline REST.** This is not about making the FastAPI app runnable without its dependencies. It
  is about the websocket transport growing the verbs it needs to stand on its own.
* **Migrating existing REST behaviour.** Nothing about the hosted viewer changes. Every step here is
  additive to the websocket path; if it changes what a REST user sees, it has gone wrong.
* **A file format.** Saving writes the procedural document that already round-trips through the
  compiler and the database. Inventing a local-only format would create a second thing to keep in
  sync with it.
