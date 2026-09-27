Command line interface
======================

The distribution is named ``ada-py``, but the console script it installs is
``ada`` — there is no ``ada-py`` command. Install the package, then call ``ada``:

.. code-block:: bash

    mamba create -n adaenv ada-py
    conda activate adaenv
    ada --help

The entry point lives in its own top-level package (``ada_cli``) so that
``ada --help`` does not import the full CAD/FEM surface. Every subcommand
imports its implementation lazily, so an invocation only pays for what it uses.

One global option applies to all of them:

``--log-level``
    Logging level for the commands that initialise the ``ada`` package
    (``convert``, ``view``, ``audit repro``, ``audit parity``). Default ``INFO``.

``convert`` and ``view`` take ``--log-file PATH`` as well -- a per-command option
rather than a global one, because argparse only accepts the global ones *before*
the subcommand and ``ada convert in out --log-file run.log`` is how it gets
typed.

Every command exits ``0`` on success. An argparse error exits ``2``, and so does
a bare command with nothing to act on — a bare invocation prints that parser's
*full* help rather than a one-line usage, but to stderr, and still exits ``2``,
because a wrong invocation must not look like success to a script. An explicit
``--help`` goes to stdout and exits ``0``.

``convert`` and ``view`` also exit ``2`` for a usage error they raise
themselves — an extension nothing can be inferred from, an output that names a
directory, an input that is not there — printed as
``ada <command>: error: <what was wrong>``. For those two, ``1`` means the
invocation was fine and the work itself failed. The commands that talk to a
hosted viewer (``files``, ``audit``, ``build``) predate this convention and
return ``1`` for a request that failed or was declined.

The command groups are ``convert``, ``view``, ``build``, ``files``, ``audit``
and ``serve``. The first two run entirely locally; the rest of this page notes
where a group talks to a hosted viewer instead.

``ada convert``
---------------

Convert a model to another format. Both ends are inferred from the file
extensions, and ``--from`` / ``--to`` override that inference.

.. list-table::
   :header-rows: 1

   * - Read (``--from``)
     - Extensions
     - Notes
   * - ``ifc``
     - ``.ifc``
     -
   * - ``step``
     - ``.step``, ``.stp``
     -
   * - ``xml``
     - ``.xml``
     - GeniE XML.
   * - ``acis``
     - ``.sat``, ``.acis``
     -
   * - ``abaqus``
     - ``.inp``
     - Also how you read a Calculix deck; they share the keyword syntax.
   * - ``sesam``
     - ``.fem``, ``.sif``
     -
   * - ``code_aster``
     - ``.med``, ``.rmed``
     -

.. list-table::
   :header-rows: 1

   * - Write (``--to``)
     - Extensions
     - Notes
   * - ``ifc``
     - ``.ifc``
     -
   * - ``step``
     - ``.step``, ``.stp``
     -
   * - ``gltf``
     - ``.gltf``, ``.glb``
     - ``.glb`` is the binary flavour.
   * - ``xml``
     - ``.xml``
     - GeniE XML.
   * - ``abaqus``
     - ``.inp``
     - Default owner of ``.inp``. One self-contained deck — the include files
       the writer uses internally are inlined and removed.
   * - ``calculix``
     - ``.inp``
     - Shares ``.inp`` with Abaqus, so it is reachable only as ``--to calculix``.
   * - ``sesam``
     - ``.fem``
     - Default owner of ``.fem``. Writes ``sestra.inp`` beside the deck when the
       model carries an analysis step.
   * - ``usfos``
     - ``.fem``
     - Shares ``.fem`` with Sesam, so it is reachable only as ``--to usfos``.
   * - ``code_aster``
     - ``.med``
     - The output is the ``.med`` mesh; the ``.comm`` command file and two
       ``.json`` maps land beside it.

Two extensions name more than one FEM format — ``.inp`` is both Abaqus and
Calculix, ``.fem`` is both Sesam and USFOS — so each has exactly one default
owner, picked to agree with what ``ada.from_fem`` already infers from a path:
``.inp`` means Abaqus, ``.fem`` means Sesam. The minority dialect is reached with
``--to`` and no other way. Inference is not silent about it: resolving a shared
extension is logged at ``INFO``, naming the flag that would have chosen the other
one.

The output argument always names **one file**, never a directory, and that exact
path is what exists when the command exits ``0``. This is worth stating because
the FEM writers do not work that way on their own: called through the Python API
they name the deck after the *model* and leave it in a scratch directory
(``<scratch>/<name>/<name>T1.FEM`` for Sesam, ``ufo_bulk.fem`` for USFOS
regardless of the name given). The CLI writes into a temporary directory next to
your output and moves the format's primary deck onto the path you asked for.

Formats that genuinely need more than one file write the rest **beside** it,
under the writer's own names — so point the output at a directory of its own when
the sidecars matter. Every path written is printed to stdout as an absolute path,
one per line, the file you asked for first and the sidecars after it:

.. code-block:: console

    $ cd /work && ada convert model.inp analysis/mesh.med
    /work/analysis/mesh.med
    /work/analysis/mesh.adapy_fem.json
    /work/analysis/mesh.comm
    /work/analysis/mesh.name_map.json

The sidecars are printed sorted by name, so the output is stable between runs.
The temporary directory the deck is built in goes away
even if the writer fails, so a failed conversion does not litter the output
directory; in the rare case it cannot be removed, a warning names it.

Sidecars keep the writer's own names, so converting twice into one directory
replaces the previous run's sidecars -- that is warned about, and giving each
conversion its own output directory avoids the question entirely.

Two consequences of that rule are worth knowing. A Sesam output may be named
either ``model.FEM`` or ``modelT1.FEM`` — the ``T1`` the Sesam writer appends is
recognised, not doubled, so a name that already has it needs no rename
afterwards. And Code_Aster output must be named ``*.med``, because the mesh is
the primary file; naming the ``.comm`` is a usage error rather than a silent
surprise.

.. code-block:: bash

    ada convert model.sat model.stp
    ada convert model.ifc model.glb
    ada convert model.inp model.FEM                  # Abaqus deck -> Sesam deck
    ada convert model.inp analysis/modelT1.FEM       # same, named as Sesam names it
    ada convert model.inp ufo/model.fem --to usfos
    ada convert model.FEM ccx/model.inp --to calculix
    ada convert deck.dat model.FEM --from abaqus
    ada convert --list-formats

``-f``, ``--from``
    Read the input as this format instead of inferring it from the extension.
    Use it for an Abaqus deck that is not named ``.inp``, or to read a Calculix
    deck (``--from abaqus``). There is deliberately no ``--from calculix``:
    adapy has no Calculix reader, and a ``.frd`` is a result file, not a model.
``-t``, ``--to``
    Write this format instead of inferring it from the output extension. This is
    the only route to ``calculix`` and ``usfos``. An explicit ``--to`` always
    wins; if it disagrees with the extension you named, the conversion still runs
    and logs a warning naming the usual extension.
``--list-formats``
    Print both tables, with the primary file each FEM writer produces, and exit
    ``0``. Works without the input and output arguments.
``--split``
    Split ACIS/SAT bodies into individual faces.
``--limit``
    Stop after this many geometries. Debugging aid; unset by default.
``--log-file``
    Write adapy's log records to this file, truncating it first, and leave only
    warnings and errors on the console. Useful on large FEM decks, where the
    ``INFO`` stream is worth keeping but not worth reading as it scrolls past.
    Without the flag nothing changes: every record at ``--log-level`` goes to
    stderr.
``--superelement``
    The Sesam super element number, written as the deck's ``IDENT`` ``SELTYP`` and
    used to name the file ``<prefix>T<N>.FEM``.

    Without it the ``T``-number in the output name decides, so ``myPrefixT10.FEM``
    is super element 10. With neither, it is 1 -- and the run says so, rather than
    leaving you to discover it from Presel. Sesam expects the number in the deck and
    the number in the file name to agree, because Presel matches them when it
    assembles, so a flag contradicting the name is a usage error rather than a
    silent override.
``--strict``
    Exit ``3`` if anything in the input could not be written to the output, or if
    the input itself looks wrong. Approximations on their own do not fail -- a tie
    resolved to the nearest node is always one. The deck and the conversion report
    are written either way; ``--strict`` reports, it does not withhold.

What a conversion could not carry across
''''''''''''''''''''''''''''''''''''''''

Every format lacks something another can say, so a conversion can be complete,
approximate, or incomplete. ``ada convert`` says which, in three places:

* a short summary on **stderr** at the end -- the status, then one line per
  finding. It is printed rather than logged, so ``--log-file`` cannot hide it,
  and it is on stderr so ``ada convert in out > paths.txt`` still captures
  nothing but paths;
* ``<OUT stem>_conversion_report.json`` beside the output, listing every finding
  with counts and measurements, and named among the written paths on stdout. It
  is written **only when there is something to report**: a clean conversion
  leaves the output file alone;
* the exit code, under ``--strict``.

A finding is one of four kinds. ``omitted`` means the construct produced nothing
in the output -- an unsupported constraint type, a keyword the reader has no
handler for. ``suspect`` means the output is faithful and valid but the *input*
looks like a modelling error, so the result is faithful to a wrong model: two
constraints making one node's degree of freedom dependent is the case it exists
for, because Sesam sums linear dependencies and would quietly add them together.
``approximated`` means something was written with different physics, and carries a
measure of the difference (a pairing distance, a dropped weight). ``note`` means
it was written faithfully and is worth a human's attention, or is plain inventory.

Omissions and suspect input fail ``--strict``; approximations and notes do not.

Findings are counted per construct, not per node: a deck that drops ten thousand
springs reports one line saying ten thousand, because a log nobody can scroll
hides an omission just as well as no log at all.

``ada view``
------------

Open the built-in web viewer on a file.

.. code-block:: bash

    ada view model.ifc
    ada view model.ifc --renderer pygfx
    ada view deck.dat --from abaqus

``-f``, ``--from``
    Read the input as this format instead of inferring it from the extension.
    Same names as ``ada convert``'s ``--from``.
``--renderer``
    One of ``react`` (default), ``pygfx`` or ``trimesh``.
``--host``
    Host to bind the viewer websocket to. Default ``localhost``.
``--ws-port``
    Websocket port. Default ``8765``.
``--split``, ``--limit``, ``--log-file``
    As for ``ada convert``.

``ada build``
-------------

Run the entrypoints declared in an ``ada_config.toml`` and push the artefacts
they produce to a viewer. The three subcommands share ``--config`` (default
``ada_config.toml``), ``--entrypoint`` (run only the named one) and
``--output-dir`` (default ``.ada-build``).

``ada build run``
    Run the entrypoints and stage the artefacts locally.
``ada build upload``
    Upload the artefacts already under the output dir.
``ada build run-and-upload``
    Chain the two. This is the one to use in CI.

Uploading needs a target and a credential, read from ``ADAPY_VIEWER_URL`` and
``ADAPY_VIEWER_TOKEN`` (the newer ``ADAPY_API_BASE`` / ``ADAPY_API_TOKEN`` pair
is accepted too, and wins when both are set). A ``.env`` in the working
directory is picked up automatically; real environment variables win over it.

``ada files``
-------------

List and move blobs in a viewer scope. Every subcommand takes ``--url``,
``--token`` and ``--scope``, each defaulting to the matching environment
variable. A scope looks like ``project:my-slug`` or ``user:me``.

``ada files list``
    List keys in the scope. ``--prefix`` filters, ``-l``/``--long`` adds sizes.
``ada files download``
    Download ``KEY`` to ``DEST`` (default: its basename in the working
    directory). Goes S3-direct through a presigned URL where the backend
    supports it; ``--via-api`` forces the tunneled GET instead.
``ada files upload``
    Upload ``SRC`` to ``KEY`` (default: the basename of ``SRC``). ``--via-api``
    forces the tunneled PUT, which is subject to the direct-upload size cap.
``ada files delete``
    Delete the given keys, and/or everything under ``--prefix``. ``-y``/``--yes``
    skips the confirmation prompt.

.. code-block:: bash

    ada files list --scope project:my-slug -l
    ada files upload model.glb versions/main/abc1234/model.glb
    ada files delete --prefix debug/ --yes

``ada audit``
-------------

A read-only client over the viewer's audit API, plus two local re-run paths.
Credentials come from ``ADAPY_API_TOKEN`` with the base URL from
``ADAPY_API_BASE`` or ``ADAPY_BASE_URL``; a bare host is accepted and gets
``https://`` prepended. Every subcommand accepts ``--url``, ``--token`` and
``--json`` (raw JSON instead of a table).

``ada audit runs``
    List recent regression-sweep runs. ``--limit`` (default 20) and ``--before``
    page through them.
``ada audit run``
    Show one run's per-cell jobs. ``--failed`` narrows to failures, ``--format``
    to a single target format.
``ada audit log``
    Query the per-conversion audit log, filtered by ``--source``, ``--target``,
    ``--status``, ``--key`` or ``--grep``, and server-side by ``--action``
    (``convert``, ``view``, ``render``, ``validate``, …) and ``--since`` /
    ``--until`` (a relative duration such as ``6h`` or an ISO-8601 instant).
``ada audit loads``
    Per-load browser model-load metrics recorded by the viewer's opt-in
    instrumentation: transport, TTFB, download, parse, prepare and first-render
    times, bytes and triangles. Filter with ``--since``, ``--until``, ``--key``
    and ``--device`` (a device-id prefix); ``--kind render`` lists steady-state
    render windows instead. A row is written only once a load completes;
    ``transport=relayed`` means the server relayed the bytes instead of a direct
    storage fetch, with the reason in ``client_metrics.fallback_reason``; and
    ``first_render_ms`` pauses while the tab is hidden.
``ada audit loads-summary``
    Per-file p50/p95 load times over ``--since-days`` (default 1), split into
    network, CPU and GPU time with the dominant bottleneck. ``--kind render``
    summarizes render windows (FPS, frame and GPU time).
``ada audit loads-hotspots``
    Function-level self-time across profiled browser loads (or render windows
    with ``--kind render``), optionally for one ``--key``.
``ada audit perf``
    Hot paths across conversions — function-level by default, cell-level when
    ``--run``, ``--worker-tag`` or ``--trigger`` is given.
``ada audit profile``
    Function stats for a single audit row's cProfile. ``--sort`` takes
    ``cumtime`` (default), ``tottime`` or ``ncalls``.
``ada audit fetch``
    Download one conversion's source blob under ``--out``
    (default ``./audit_repro``).
``ada audit logfile``
    Download a conversion's captured stdout/stderr. Prints to stdout unless
    ``--out`` names a file.
``ada audit repro``
    Fetch a conversion's source and run it locally, optionally against a
    different ``--target`` format.
``ada audit wasm-sweep``
    Re-run a remote run's cells locally through the in-browser WASM engine
    (node-pyodide) and write a pass/fail report. Writes nothing back to the
    database.
``ada audit parity``
    Cross-format visual-parity check on a local model: export to each of
    ``--formats`` (default ``ifc,xml,step``), reload, and compare the visualized
    element counts.

.. code-block:: bash

    ada audit runs --limit 5
    ada audit run 42 --failed
    ada audit repro 1234 --target step
    ada audit loads --since 2h --device 3f2a9c1e
    ada audit loads-summary --since-days 7

``ada serve``
-------------

Run one of the long-lived server processes. Neither subcommand takes options —
both are configured entirely through the environment.

``ada serve api``
    Run the REST API under uvicorn.
``ada serve worker``
    Run the conversion worker (a NATS JetStream consumer).
