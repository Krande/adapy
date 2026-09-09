DEXPI: P&ID in, routed 3D model out
====================================

`DEXPI <https://dexpi.org/>`_ (Data Exchange in the Process Industry) is the vendor-neutral
exchange specification for a P&ID: which equipment exists, which nozzles it has, and which piping,
signal and electrical lines connect them -- with no notion whatsoever of physical size or plant
coordinates. ``ada.cadit.dexpi`` reads and writes it, and ``ada.from_dexpi`` closes the gap between
"here is a P&ID" and "here is a routed 3D model": it resolves each item to a physical equipment
definition, generates a plausible topology to put the equipment in, turns the P&ID connectivity into
adapy systems, and routes them.

Nothing here is imported by ``import ada`` -- ``ada.cadit.dexpi`` and its readers load lazily, so
the DEXPI support costs nothing until it is used.

Two wire formats, one model
----------------------------

DEXPI 2.0.0 (2025-10-10, CC BY 4.0) replaced the *serialization* without changing plant semantics.
adapy reads and writes both, dispatched on the root tag:

.. list-table::
   :header-rows: 1

   * -
     - DEXPI 1.3/1.4 ("Proteus XML")
     - DEXPI 2.0.0 ("DEXPI XML")
   * - Root element
     - ``<PlantModel>``
     - ``<Model>``
   * - Class
     - ``ComponentClass="CentrifugalPump"``
     - ``type="Plant/ProcessEquipment.CentrifugalPump"``
   * - Attributes
     - ``<GenericAttribute Name Value Format Units/>``
     - ``<Data property><String>``/``<Double>``/``<AggregatedDataValue>``
   * - Connectivity
     - ``<Connection FromID FromNode ToID ToNode>`` (positional node indices)
     - ``<References property="SourceItem" objects="#Nozzle6"/>`` (ID-based)
   * - Files in the wild
     - every vendor export; the bulk of the official test corpus
     - essentially only the specification's own reference P&ID

Proteus is the default and the format almost every real emitter still writes; DEXPI 2.0 support
exists and is tested against the specification's own reference P&ID, but real-world 2.0 files are
still rare. Both converge on one neutral, in-memory :class:`~ada.cadit.dexpi.model.DexpiDocument`,
so nothing downstream of the reader -- equipment resolution, layout, systems -- cares which flavour
a file came in as.

The class table behind item resolution (``ada.cadit.dexpi.class_table``) is generated once from the
CC BY 4.0 DEXPI 2.0.0 specification repository and checked in as JSON -- 341 classes, 142 of them
under ``Plant/ProcessEquipment``. Nothing here performs a live RDL lookup or XSD validation; both
are offline by policy (see *Never*, below).

Reading a P&ID
--------------

``ada.from_dexpi`` is the one-call path from a file to a built, routed :class:`ada.Assembly`:

.. code-block:: python

    import ada
    from ada.topo_model.layout import LayoutRules

    a = ada.from_dexpi(
        "files/dexpi_files/unit_separator_proteus.xml",
        layout=LayoutRules(max_length=24.0, max_width=12.0, deck_height=5.0),
    )

    print(sorted(eq.name for eq in a.get_all_parts_in_assembly() if isinstance(eq, ada.Equipment)))
    # ['E-201', 'P-201A', 'P-201B', 'TE-203', 'TE-204', 'V-201']

    print(sorted(s.name for s in a.systems))
    # ['201/1', '202/1', '203/1', '203/2', '203/3', '204/1', '204/2', '204/3', '206/1']

    a.to_ifc("separator_unit.ifc")

Three steps happen inside (:mod:`ada.cadit.dexpi.read.to_procedural`):

1. **Equipment.** Every item that ``is_a(cls, "ProcessEquipment")`` resolves -- per-tag override,
   then per-class override, then the shipped class default -- to an envelope, an IFC class and a
   port per actual nozzle, placed on the box by a class-specific strategy (``vessel``/``pump``/
   ``exchanger``/``generic``). A nested ``Chamber`` (a separator's boot) folds into its owning
   equipment rather than becoming an asset of its own. A **branch point** -- an in-line fitting
   that more than one segment ends at, typically a ``PipeTee`` -- is materialised too, as a small
   ``IfcPipeFitting`` with one port per connection node; ``TE-203`` and ``TE-204`` above are the
   two in this file, and they are why the runs through them exist at all.
2. **Layout.** The resolved envelopes go to :func:`ada.topo_model.layout.plan_layout`, which
   generates deck spaces and shelf-packs equipment onto them -- see *The generated layout is not a
   plot plan*, below.
3. **Systems.** One :class:`~ada.topology.entities.TopoSystem` per DEXPI ``PipingNetworkSegment``,
   named ``<line number>/<segment number>``, then routed with the standard design rules. A
   ``PipeOffPageConnector`` becomes a site terminal (``is_site=True``); the parent
   ``PipingNetworkSystem`` survives as the run's medium and provenance metadata.

The above example is the checked-in ``unit_separator_proteus.xml`` fixture, and it is a good
illustration of an honest gap, not a cherry-picked clean run: 9 of its 10 DEXPI segments turn into
routed systems and the tenth is reported rather than dropped. That last one, ``205/1``, is a relief
valve discharging to something the P&ID does not draw -- a genuinely one-ended run, and
:func:`~ada.topology.routing.route_system` routes exactly ``ports[0] -> ports[-1]``, so there is
nothing to route it to. **Nothing is dropped silently**: every system or equipment that did not
reach the 3D model is collected into ``assembly.metadata["dexpi"]["report"]`` and summarised in one
``logger.warning`` line; :func:`~ada.cadit.dexpi.read.to_procedural.dexpi_import_report` renders it
as a table:

.. code-block:: text

    1 of 10 system(s) and 0 of 6 equipment did not reach the 3D model:

    Kind    Name   Stage         Reason
    ------  -----  ------------  -----------------------------------------------------------------
    system  205/1  connectivity  1 endpoint(s) outside the segment; a routed run needs exactly two

Six of those nine runs meet at one of the file's two tees, and used to be lost with them: a segment
whose end names a ``PipeTee`` names neither a nozzle nor an equipment nor an off-page connector.
The junction itself still never becomes one system -- ``route_system`` has no branch concept, and
that is why there is one system per DEXPI segment -- but each run *into* a junction is an ordinary
two-ended run once the fitting it ends at is a real placed object with real ports.

Pass ``strict=True`` to raise instead of returning a partially-built model with a warning. Other
useful arguments: ``definitions`` (the equipment override list, see below), ``base_doc`` (merge onto
placements you already corrected, for a non-destructive re-import), ``route=False`` (place equipment
without routing), ``inline_components="equipment"`` (materialise in-line valves/strainers as their
own small ``ada.Equipment`` instead of run metadata), and ``build_3d=False`` (the schematic-only
assembly -- equipment and ports, no structure or routing).

``ada.dexpi_to_procedural(path, ...)`` exposes the intermediate seam -- the plain procedural
document plus the equipment catalog, ready for ``ProceduralBuilder.from_dict`` or ``to_excel``
before anything is built.

Equipment definitions
----------------------

A P&ID says a vessel is a ``ProcessColumn``; it never says how big it is. adapy ships a hand-curated
table (``ada.cadit.dexpi.equipment_defaults``, backed by
``resources/dexpi_equipment_defaults.json``) of **29 curated DEXPI classes** -- envelope, IFC
element class, nozzle-layout strategy and an apparent density -- resolved up the supertype DAG so
all 142 ``Plant/ProcessEquipment`` classes are covered (a ``CentrifugalPump`` inherits ``Pump``'s
entry). Nozzle *positions* are always generated per item from its actual DEXPI nozzles, never fixed
per class, because nozzle count varies item by item.

These are placeholders for real vendor data, not engineering deliverables, and are meant to be
overridden. An equipment definition list (:mod:`ada.cadit.dexpi.equipment_list`) supplies full
overrides keyed by **tag** (wins) or **DEXPI class** (falls back), as either JSON
(``{tag_or_class: EquipmentTypeDoc}``) or a two-sheet XLSX workbook (``EquipmentTypes`` + a joined
``Nozzles`` sheet, for hand-editing many nozzles at once). Pass a path or an already-loaded dict as
``ada.from_dexpi(..., definitions=...)``.

Port ``category`` (``process``/``signal``/``electrical``) is not cosmetic: ``System.connect`` raises
on a category mismatch and the compiler then drops the *whole* system with only a warning. One trap
worth knowing about because it is easy to get backwards: the DEXPI spec makes ``Nozzle`` a subtype
of both ``ActuatingElectricalLocation`` and ``SensingLocation``, so a naive "is this an electrical
location?" test types every ordinary process nozzle in the model as electrical.
``ada.cadit.dexpi.nozzle_placers.category_for`` resolves the ``Nozzle`` family first, before any
instrumentation supertype test, for exactly that reason.

The generated layout is not a plot plan
------------------------------------------

There is no DEXPI concept of plant coordinates, and adapy does not invent good ones.
``ada.topo_model.layout`` (a general capability, not DEXPI-specific) is **shelf packing on physical
footprint alone**: sort equipment by descending footprint, pack rows onto a deck within
``LayoutRules.max_length``/``max_width``, start a new deck when one fills. It has no process sense
whatsoever -- a pump can land at the far end of a deck from the vessel it feeds. Equipment connected
by a segment is packed as one contiguous group, which is a cheap first heuristic and not a
substitute for engineering judgement.

Use ``layout=LayoutRules(...)`` to size the decks, and pass an explicit ``deck_height``: the pitch
is uniform across the whole plan, so leaving it unset makes it "the tallest item plus headroom" --
one 15 m ``ProcessColumn`` would make *every* deck 16 m tall. ``base_doc`` lets a re-import keep
placements you have already corrected by hand instead of regenerating them.

Once a model is built and routed, :func:`ada.topo_model.relocate.propose_relocations` is the
existing follow-up tool: it looks at which runs did not route cleanly and proposes the equipment
moves that would clear them. Plan first with ``from_dexpi``, relocate after.

What is modelled, what is not
--------------------------------

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Modelled
     - Equipment (142 ``Plant/ProcessEquipment`` classes -> IFC class, size default, nozzle
       placement rule); ``Nozzle`` -> ``Port`` (flow direction, DN, spec, category, with the
       ``Nozzle``-is-not-electrical rule above); ``Chamber`` -> nested equipment (folded into its
       owner); ``PipingNetworkSegment`` -> a two-ended ``TopoSystem``/piping system; the connection
       graph; ``PipeOffPageConnector`` -> a site terminal; a branch point (a passive fitting more
       than one segment ends at, e.g. a ``PipeTee``) -> an ``IfcPipeFitting`` equipment with a port
       per connection node, so each run meeting there stays two-ended and routes; in-line components
       (as run metadata by default, or opt-in equipment);
       ``TagName``/``LineNumber``/``FluidCode``/``PipingClassCode``/
       nominal diameter; every import gap collected into ``assembly.metadata["dexpi"]["report"]``
       rather than dropped silently.
   * - Deferred to metadata + verbatim echo
     - ``InstrumentComponent``, ``ActuatingSystem``, ``InstrumentationLoopFunction``,
       ``MeasuringSystem``, ``PropertyBreak``, ``PlantStructureItem``, ``Association`` links,
       ``ShapeCatalogue``/``Presentation``/``Drawing``/``Label``, ``CenterLine``/``PolyLine``
       geometry, custom attribute sets, physical quantities with units (parsed to
       ``(value, unit_uri)``; SI-converted only for nominal diameter),
       ``Position``/``Axis``/``Reference`` rotation angles. None of this drives geometry, but none
       of it is thrown away either -- everything an item's source ``ET.Element`` does not model
       stays on ``DexpiItem.raw``/``extras`` for a future writer to re-emit.
   * - Deferred to follow-up work
     - Clustering equipment by room requirements into per-group cells (the ``group_key`` seam on
       ``LayoutRules`` exists for it); ``PlantStructureItem`` -> an ``ada.Part`` hierarchy;
       DEXPI-driven duct systems (a P&ID has no ducting concept at all -- ``"duct"`` is only ever
       produced when a definition list marks a line as one); routing across multiple decks with
       vertical shafts; feeding ``relocate.propose_relocations`` back into the layout automatically
       when a run fails to route; a genuinely one-ended segment -- a relief valve discharging to
       something the P&ID does not draw (``205/1`` in the worked example above) -- which has nothing
       to route to and so lands in the import report rather than the model; routing a junction as
       *one* branched system rather than as the several two-ended runs that meet at it, which needs
       branch/tee support in ``route_system`` that does not exist yet (the runs themselves do route
       -- see the branch-point row above -- but no single ``System`` spans the tee, and no fitting
       geometry is modelled at the junction beyond the placed ``IfcPipeFitting`` box).
   * - Never
     - Live RDL registry lookups (the symbol table ships with an out-of-tree package adapy does not
       depend on); XSD validation of DEXPI 2.0 (would need ``lxml``/``xmlschema`` -- structural
       checks in ``ada.cadit.dexpi.validate`` stand in instead). Both offline by policy: adapy has no
       network dependency at parse time.

Two real adapy gaps surfaced by this work, worth naming rather than working around:
``ada.Equipment`` has no placement frame, so ``Port.get_global_position`` ignores rotation (harmless
today because equipment rotation is baked into each port's position when it is placed); and
``route_system`` has no branch/tee support, which is the reason a DEXPI segment must be two-ended to
route at all. The second is worked around rather than closed: materialising a branch point as
equipment gives every run at a tee two real ends, but the tee is still three runs meeting at a box,
not one branched system.

Writing back to DEXPI
------------------------

``Assembly.to_dexpi(destination, flavour="proteus", from_scratch=False)`` writes an adapy model back
out. It is a **merge, not a regeneration**: starting from the source ``DexpiDocument`` an assembly
carries after ``from_dexpi`` (or a sidecar attached by hand), it re-serializes everything adapy owns
from the live objects so edits made in Python land in the file, re-emits everything it does not
model verbatim from ``DexpiItem.raw``/``extras``, drops items deleted in adapy along with their
connections (logged), and mints fresh IDs for anything adapy added. ``from_scratch=False`` with no
source document raises, pointing at the flag -- writing a from-scratch model
(``from_scratch=True``, e.g. from a hand-built ``ada.topo_model`` archetype assembly) is lossy by
construction and only claims that the equipment/port/system graph survives a re-read, not a
byte-for-byte anything.

Running the wider corpus locally
-----------------------------------

The DEXPI test suite ships two official CC BY 4.0 example files under ``files/dexpi_files/vendor/``
so the reader/writer are exercised against real, unvendored emitter output without any network
access. ``scripts/fetch_dexpi_testcases.py`` downloads the full official
`TrainingTestCases <https://gitlab.com/dexpi/TrainingTestCases>`_ corpus (currently around 220 P&IDs
spanning DEXPI 1.2 and 1.3) into the git-ignored ``files/dexpi_files/_external/``:

.. code-block:: console

    $ python scripts/fetch_dexpi_testcases.py
    $ pixi run -e tests pytest tests/core/cadit/dexpi/test_external_corpus.py

``tests/core/cadit/dexpi/test_external_corpus.py`` parses every file under ``_external/`` and
asserts the same lossless-echo round-trip (``file -> doc -> XML -> doc'`` canonically equal) the
checked-in fixtures are held to. It is skipped, not run, whenever that directory does not exist, so
this stays purely a local, opt-in tool -- ``pixi run -e tests test-core`` and CI never touch the
network. A run against the full corpus is a good pre-release check: it is what caught the 0-based
(rather than 1-based) positional node indexing that DEXPI 1.3 actually uses in the wild, which a
smaller fixture set would not have exposed.

It has since earned its keep twice more, on the first full run after the writers landed -- 15 of
the 220 files failed, both causes invisible to any fixture the branch had:

*A connection nested in an element the model does not carry was written twice.* The
``<Connection>`` inside an ``<InformationFlow>`` is echoed verbatim along with its owner *and* was
re-emitted from ``doc.connections`` at document level, so an unedited round-trip gained a second,
owner-less copy of the edge -- connectivity the P&ID never had. 14 files, DEXPI 1.2 signal
connectivity above all. The writer now skips any connection whose source element the echo already
carries, tested by element identity against the source parse.

*A full RDL URI in* ``ComponentClass`` *changed the class on the way through.* Emitters are not
supposed to write ``ComponentClass="http://sandbox.dexpi.org/rdl/ProcessInstrumentationFunction"``,
and some do. ``class_table.resolve`` took the last *dot* before the last slash, which lands in the
host name and yields the class ``org/rdl/ProcessInstrumentationFunction``; the writer emitted that
and the reader then resolved it differently coming back in. Taking the path segment off first fixes
it and makes ``resolve`` idempotent, which is the property the round-trip actually depends on.

Both are pinned by checked-in fixtures in ``tests/core/cadit/dexpi/test_write_proteus.py``, so CI
holds them without the corpus. Note that fetching the corpus puts third-party code in the tree:
``pyproject.toml`` names ``_external/`` in isort's skip list, because isort -- unlike black and
ruff -- does not read ``.gitignore`` and would otherwise fail ``pixi run lint-check`` on it.

Licensing and attribution
----------------------------

The generated class table, the two vendored official test files, and anything fetched by
``scripts/fetch_dexpi_testcases.py`` are Creative Commons Attribution 4.0 International (CC BY 4.0),
copyright DEXPI e.V. -- adapy's own code and curated defaults are not. See the repository-root
``NOTICE`` file for the full attribution, and ``files/dexpi_files/vendor/NOTICE.md`` for the
file-level detail on the two vendored test cases.
