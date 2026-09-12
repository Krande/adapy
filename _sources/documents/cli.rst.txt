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

The command groups are ``convert``, ``view``, ``build``, ``files``, ``audit``
and ``serve``. The first two run entirely locally; the rest of this page notes
where a group talks to a hosted viewer instead.

``ada convert``
---------------

Convert a model to another format. Input formats: ifc, step/stp, xml, inp, fem,
sat/acis. Output formats: ifc, step/stp, gltf/glb, xml, inp. The output format
is inferred from the destination extension.

.. code-block:: bash

    ada convert model.sat model.stp
    ada convert model.ifc model.glb

``--split``
    Split ACIS/SAT bodies into individual faces.
``--limit``
    Stop after this many geometries. Debugging aid; unset by default.

``ada view``
------------

Open the built-in web viewer on a file.

.. code-block:: bash

    ada view model.ifc
    ada view model.ifc --renderer pygfx

``--renderer``
    One of ``react`` (default), ``pygfx`` or ``trimesh``.
``--host``
    Host to bind the viewer websocket to. Default ``localhost``.
``--ws-port``
    Websocket port. Default ``8765``.
``--split``, ``--limit``
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
    ``--status``, ``--key`` or ``--grep``.
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

``ada serve``
-------------

Run one of the long-lived server processes. Neither subcommand takes options —
both are configured entirely through the environment.

``ada serve api``
    Run the REST API under uvicorn.
``ada serve worker``
    Run the conversion worker (a NATS JetStream consumer).
