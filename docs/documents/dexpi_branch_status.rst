DEXPI branch: status and continuation notes
=============================================

.. note::
   This is a **working document for the ``feat/dexpi`` branch**, not user-facing feature
   documentation -- that is :doc:`dexpi`. It exists so the branch can be picked up cold, by another
   agent or another person, without re-deriving what has already been decided and verified. Delete
   it (or fold anything still true into :doc:`dexpi`) once the branch merges.

Where things stand
-------------------

``feat/dexpi`` (pushed to ``origin/feat/dexpi``, cut from ``main`` at ``73b3498e``) implements
**DEXPI P&ID import, generated 3D layout and routing, and DEXPI export** for adapy. The commit log
on the branch is the authoritative history; this document summarises it and records *why* things
ended up the way they did, which the log alone does not carry.

As of this writing the branch carries:

.. code-block:: text

    feat(dexpi): merge writer, Assembly.to_dexpi, pickle case (PR 10)
    fix(dexpi): import report summary compared dropped count against the wrong denominator
    feat(dexpi): corpus fetch script, guarded corpus test, gap-report docs (PR 11)
    feat(dexpi): Proteus and DEXPI 2.0 writers, example fixtures, vendored corpus (PR 5)
    fix(topo_model): register wired systems on Assembly.systems
    feat(dexpi): DEXPI P&ID to routed 3D model (PR 9)
    feat(dexpi): DEXPI 2.0 reader and the flavour-dispatching store (PR 4)
    feat(dexpi): equipment class defaults, nozzle placers and the definition list (PR 8)
    feat(dexpi): Proteus reader with two-pass positional node normalization (PR 3)
    feat(systems): carry process identity on ports, equipment and system segments (PR 7)
    feat(dexpi): neutral in-memory model, flavour sniffing and the test oracles (PR 2)
    feat(dexpi): vendor the DEXPI 2.0.0 class table (PR 1)
    feat(topo_model): rule-based topology generation (ada.topo_model.layout) (PR 6)

Every one of these has been run independently (not just trusted from the agent that wrote it):
``pixi run -e tests pytest tests/core/cadit/dexpi tests/core/topo_model tests/core/topology
tests/core/systems`` passes in full, ``pixi run lint-check`` is clean, and the branch diff against
``main`` has been grepped for local paths, usernames, host names and organisation identifiers with
zero hits at every checkpoint.

**A fix for a real bug found by manually running the flagship example may be in flight when you
read this** -- check the git log for a commit touching
``src/ada/cadit/dexpi/read/to_procedural.py`` after the ones listed above, and see "Known gap:
branch points at passive fittings" below before assuming it is still open.

The one pre-existing, unrelated test failure on a Windows checkout with a non-UTF-8 default
codepage is ``tests/core/cadit/step/test_ada_ext_codegen.py::test_ada_ext_header_matches_schema``
(a ``read_text()`` call with no explicit encoding, decoding a committed em-dash as mojibake). It
predates this branch, is not caused by anything here, and should not be "fixed" as part of it.

How to resume
--------------

.. code-block:: powershell

    git fetch origin
    git checkout feat/dexpi   # or: git worktree add ../adapy-dexpi feat/dexpi
    pixi run -e tests pytest tests/core/cadit/dexpi tests/core/topo_model tests/core/topology tests/core/systems -q
    pixi run lint-check

The user-facing entry points are ``ada.from_dexpi(path, ...)`` and ``Assembly.to_dexpi(path, ...)``
-- see :doc:`dexpi` for the full picture, including the gap table (what DEXPI content adapy models,
defers to metadata, or does not attempt at all).

Architecture, in one pass
--------------------------

.. code-block:: text

    src/ada/cadit/dexpi/
        model.py, canonical.py, flavour.py, validate.py, class_table.py, units.py, attributes.py
        resources/dexpi_classes.json              # generated from the DEXPI 2.0.0 spec repo, CC BY 4.0
        resources/dexpi_equipment_defaults.json   # hand-curated adapy data, NOT DEXPI-authored
        equipment_defaults.py, nozzle_placers.py, equipment_list.py
        read/read_proteus.py, read/read_dexpi20.py, read/to_procedural.py
        write/write_proteus.py, write/write_dexpi20.py, write/from_ada.py
        store.py
    src/ada/topo_model/layout.py                  # general, DEXPI-independent: shelf-packs
                                                    # equipment into decks; DEXPI is its first caller
    src/ada/factories.py                           # ada.from_dexpi, ada.dexpi_to_procedural
    src/ada/api/spatial/assembly.py                # Assembly._dexpi_store, Assembly.to_dexpi
    src/ada/api/systems/{ports,segments,base}.py   # Port.tag/nominal_diameter/spec/metadata,
                                                    # SystemSegment, System.segments
    files/dexpi_files/                             # fixtures: tiny + realistic, both wire formats,
                                                    # plus two vendored official CC BY 4.0 test files
    tests/core/cadit/dexpi/
    tests/core/topo_model/test_layout.py
    docs/documents/dexpi.rst                       # the user-facing feature doc
    scripts/gen_dexpi_class_table.py, gen_dexpi_examples.py, fetch_dexpi_testcases.py

The pipeline: a DEXPI file (either wire format) parses to one neutral
:class:`~ada.cadit.dexpi.model.DexpiDocument`. Each equipment item resolves through the definition
list to a physical envelope with real 3D nozzles (class defaults, overridable per tag or class).
:func:`~ada.topo_model.layout.plan_layout` packs the resolved equipment into generated decks. Each
DEXPI ``PipingNetworkSegment`` becomes one two-ended ``TopoSystem`` -- the load-bearing design
decision, because :func:`ada.topology.routing.route_system` routes exactly two ports and has no
branch/tee concept. The result compiles through the existing (pre-branch) procedural pipeline into
a routed :class:`ada.Assembly`. Nothing is dropped silently: every system the compiler could not
wire or route, and every equipment the layout could not place, is collected into
``assembly.metadata["dexpi"]["report"]`` and summarised in a warning.

Findings worth knowing before you touch this code
----------------------------------------------------

These were not obvious from the DEXPI specification and were each found by actually running the
code against the official test corpus or the realistic generated fixture -- not by reading the
spec. Get them wrong and the importer parses cleanly while silently producing a wrong model.

Proteus connection indices are 0-based, not 1-based
    ``<Connection FromNode="1">`` addresses the *second* entry (index 1) of the owner's
    ``ConnectionPoints`` list, counting the symbol anchor as index 0 -- proven against the official
    corpus (55 unambiguous cases in DEXPI 1.3, 0 counter-examples). Getting this backwards makes the
    reader resolve every connection to the anchor, silently and without error, because index 1 is
    always in range. The writer must emit 0-based indices too, pinned by an explicit test that
    checks the literal integer in the output XML.

A plain ``Nozzle`` is a DEXPI subtype of ``ActuatingElectricalLocation``
    Testing "is this an electrical location?" by supertype alone types every process nozzle in the
    model as electrical, which ``System.connect`` then refuses -- silently dropping the system with
    only a log warning. Resolve the ``Nozzle`` family before any instrumentation supertype test.

There is no DEXPI class literally named ``Equipment``
    The abstract base is ``ProcessEquipment``. ``Chamber`` and ``Nozzle`` do **not** derive from it
    (both come straight off ``Core/ConceptualObject``) -- classify them by explicit class name, not
    by supertype.

RDL class URIs (``ComponentClassURI``) cannot be synthesised
    They are not in the DEXPI 2.0.0 specification repository (which uses symbolic RDL references);
    only the source document's own URI can be echoed on a merge write, and it must be omitted, not
    guessed, on a from-scratch write.

Deck placement requires ``LX``/``LY``/``LZ`` to be stamped explicitly
    The procedural compiler requires them before the catalog-bbox fallback is ever reached
    (``ada.topo_model.compile._require_coords``), so the layout module stamps them from the
    resolved equipment bbox rather than relying on the catalog resolver at compile time.

Deck pitch is uniform across a layout plan, not chosen per deck
    "Tallest item on this deck plus headroom" is circular once pre-placed (``fixed``) items exist --
    you need deck elevations to assign an item, and the item to compute the elevations. Pass an
    explicit ``deck_height``, or one tall ``ProcessColumn`` makes every deck as tall as it is.

``assembly.systems`` was never populated by the procedural compiler
    ``_build_systems`` built and routed each ``System`` object but never handed it back to its
    caller, so ``write_ifc_systems`` had nothing to read: every procedural (not just DEXPI) IFC
    export was silently missing ``IfcRelConnectsPorts`` and correct ``IfcDistributionSystem`` naming
    and ``PredefinedType``. Fixed by an optional ``built_systems_out`` out-parameter threaded through
    ``ProceduralBuilder.build_systems``. Verified directly against exported IFC entity counts, not
    just by tests passing.

A shared branch point (e.g. a ``PipeTee``) is not the same problem as "no branch support"
    ``route_system`` genuinely cannot route a 3+-way junction as one system, and the plan's
    segment-per-system design is the right answer to that. But a passive fitting referenced as an
    endpoint by *more than one* ``PipingNetworkSegment`` is a different case: it needs a small
    materialised equipment-with-ports at the junction so each segment still resolves as a proper
    two-ended run. This was found by running the checked-in realistic fixture
    (``files/dexpi_files/unit_separator_proteus.xml``) end to end with the default layout -- it has
    two such tees -- something none of the automated tests happened to exercise, because the
    importer's own test fixture and the example-file generator's fixture were built by different
    people at different times and never run together until this was checked by hand. **Check the
    git log to see whether the fix for this landed** (a commit touching
    ``read/to_procedural.py`` after PR 10); if not, it is the next thing to do, and the brief that
    was written for it is preserved in git history / on the task that produced this document.

Process notes for whoever continues this
-------------------------------------------

- **Never use bare** ``git stash`` **/** ``git stash pop`` **on this machine** -- the stash stack is
  shared across worktrees and sessions. Use a WIP commit if you need to set work aside; that is what
  saved an in-progress PR here when a session hit a rate limit mid-task.
- **Never** ``git commit --amend`` **once other work may have landed on the branch** -- it happened
  once during this work (an agent amended after ``HEAD`` had moved), was caught immediately via the
  reflog, and was recovered with ``git reset --soft`` rather than losing anything. Prefer a new
  commit.
- **No local machine, username, or organisation identifiers may appear in anything committed** --
  absolute paths, host names, private IPs, employer name. Grep the diff before every commit. This
  repo is public.
- Independently re-run whatever an agent reports rather than trusting the summary -- several of the
  findings above (the ``assembly.systems`` gap, the branch-point gap, an import-report phrasing bug)
  were caught only by actually running the code by hand against a real fixture, not from a test
  suite passing.
