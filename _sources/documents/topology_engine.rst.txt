Topology-based procedural modelling
===================================

``ada.topology`` is a domain-generic procedural engine: you describe a model as
a set of *spaces* (boxes), the engine partitions them into a cell graph with
classified faces and edges, and a *blueprint* turns that topology into
geometry. ``ada.topo_model`` is the in-repo reference implementation — a small
steel structure with equipment, service systems and routed connections.

The engine in a nutshell
------------------------

Spaces go in, an assembly comes out:

.. code-block:: python

    import ada
    from ada.topology import TopologyBuilder
    from ada.topo_model import SteelStru

    boxes = [
        ada.PrimBox("Cell1", (0, 0, 0), (5, 5, 3)),
        ada.PrimBox("Cell2", (5, 0, 0), (10, 5, 3)),
    ]
    builder = TopologyBuilder.from_prim_boxes(boxes, blueprint=SteelStru())
    builder.build()
    a = builder.get_output_assembly("MyModel")

The ``CellGraph`` behind the builder answers topology questions: which faces
are external floors or walls, which wall is shared between two cells
(``get_internal_walls``), and each face carries its ordered outline points,
edges and normal. Two adjacent cells share one wall and the girder/column edges
along it — the engine deduplicates those for you.

Or in one line, with the demo's defaults:

.. code-block:: python

    from ada.topo_model import build_topo_model

    a = build_topo_model()

Writing a blueprint
-------------------

A blueprint subclasses :class:`~ada.topology.blueprint.BlueprintBase` and
implements ``build()``: read the cell graph, emit parts, register them per
area with ``add_to_area`` and finish with ``load_parts_from_area_map()``:

.. code-block:: python

    import ada
    from ada.topology import BlueprintBase

    class MyStru(BlueprintBase):
        def _group_prefix(self) -> str:
            return "MyStru"

        def build(self) -> ada.Part:
            self.output_part = ada.Part("MyStru")
            cg = self.builder.cell_graph
            for i, face in enumerate(cg.get_external_floors()):
                plate = ada.Plate.from_3d_points(f"pl{i}", face.get_points(), 0.01)
                self.add_to_area("floors", ada.Part(f"Floor_{i}") / plate)
            self.load_parts_from_area_map()
            return self.output_part

``ada.topo_model.SteelStru`` is the full worked example: reinforced floors
(plate + evenly spaced stringers), girders from deduplicated floor-face edges
and columns from the vertical wall edges.

Equipment with ports
--------------------

Equipment carries typed connection points — *ports* — at local positions with
outward directions and a service category (``process``/``electrical``/``signal``):

.. code-block:: python

    import ada

    pump = ada.Equipment("P1", mass=1000, cog=(0, 0, 0.5), origin=(2.5, 2.5, 3.0), lx=1, ly=1, lz=1)
    pump.add_port(ada.Port("discharge", (0, 0, 1.0), (0, 0, 1), ada.PortDirection.OUT, "process"))
    pump.add_port(ada.Port("power", (0.5, 0, 0.5), (1, 0, 0), ada.PortDirection.IN, "electrical"))

The demo archetypes ``create_pump`` / ``create_tank`` in ``ada.topo_model``
ship with realistic port layouts, and ``ada.Voltage`` enumerates typical
industrial supply levels (230 V – 11 kV).

Wiring systems
--------------

A ``System`` is a logical service network with a fixed category; connecting it
to ports is fluent and fail-fast (wrong category or an already-connected port
raises with a clear message):

.. code-block:: python

    cooling = (
        ada.PipingSystem("CoolingWater", medium="water")
        .connect(pump, "discharge")
        .connect(tank, "inlet")
    )
    power = ada.ElectricalSystem("PowerFeed", voltage=ada.Voltage.LV_690).connect(pump, "power")

The references are bidirectional: ``port.connected_system`` points at the
system, ``system.ports`` / ``system.connected_equipment`` point back.

Routing
-------

Systems route over a :class:`~ada.topology.grid.CellGrid` node lattice with
6-connected A*; occupied nodes are avoided and the routed polyline keeps only
its bends:

.. code-block:: python

    from ada.topology import CellGrid, RoutingRules

    grid = CellGrid.from_bounds((0, 0, 3.0), (10, 5, 5.5), spacing=0.5)
    grid.register(grid.index_of(5.0, 2.5, 3.5), "obstruction")

    cooling.route(grid)                 # default rules
    power.route(grid, rules=RoutingRules(elevation_penalty=5.0, bend_penalty=1.0))

Rules are pluggable: ``is_allowed`` / ``move_cost`` callables plus elevation
and bend penalties. ``system.route(...)`` also generates the route geometry
matched to the service: a round ``ada.Pipe`` run (with auto-inserted elbows)
for piping, a rectangular BOX ``ada.Beam`` run for ducting, and an open
UNP-channel ``ada.Beam`` run for cable trays / electrical. For
blueprint-driven routing, subclass
:class:`~ada.topology.routing.RoutingBlueprintBase` and override ``rules_for``
per system and/or ``build_routing_grid``.

Penetrations
------------

Where a routed system crosses a wall or floor, a penetration blueprint turns
the crossing into a detail. ``StandardPenetrations`` keys the detail on the
routing type — a pipe sleeve for process runs, an MCT-style transit block for
cable/electrical, a rectangular frame for ducts — and cuts the through-hole in
the crossed face's built wall plate:

.. code-block:: python

    from ada.topo_model import StandardPenetrations

    pens = StandardPenetrations(systems=[service], faces=cg.get_internal_walls())
    a.add_part(pens.build())   # one detail part per crossing; wall plates get the hole

The demo builds its shared internal wall as a reinforced wall
(``SteelStru(reinforce_internal_walls=True)`` — plate + vertical stiffeners)
and routes an interior service run straight through it; subclass
:class:`~ada.topo_model.penetration.PenetrationBlueprintBase` and override
``build_penetration`` for your own detail standard.

Pluggable design rules
----------------------

Routing and penetration rules can be handed to the engine as plain callables —
no subclassing. The engine runs in **two phases**, and every rule is a function
that *fully encompasses* its stage:

* **Plan** (geometry-free, runs first over the whole cell complex): a
  ``plan_route`` callable turns ``(system, cell complex, grid)`` into a
  :class:`~ada.topology.design_rules.RoutePlan`, and a ``plan_penetration``
  callable turns ``(system, routed path, penetrated members)`` into a list of
  :class:`~ada.topology.design_rules.Penetration` crossings. Planners see the
  ``CellGraph`` (the cell complex — cells + classified faces) and the routing
  ``CellGrid`` lattice, plus the penetrated members for penetration rules. They
  emit *data*, never geometry.
* **Model** (plan → geometry): a ``model_route`` callable turns a ``RoutePlan``
  into adapy geometry, and a ``model_penetration`` callable turns a
  ``Penetration`` into a detail part.

:class:`~ada.topology.design_rules.DesignRules` bundles the four callables and
:func:`~ada.topology.design_rules.run_design` drives both phases in order (plan
everything, then model everything), returning a
:class:`~ada.topology.design_rules.DesignResult` — the routed geometry
(``route_geometry`` keyed by system name), the planned ``penetrations`` and their
``penetration_parts``, and any ``skipped`` systems (see ``skip_failed`` below).
The defaults reproduce the built-in routing; supply your own callables to fully
override a stage:

.. code-block:: python

    from ada.topology import DesignRules, RoutePlan, RoutingRules, run_design
    from ada.topo_model import standard_design_rules

    # A planning rule that fully encompasses routing: forbid a keep-out zone and
    # prefer a fixed service elevation. Planners return data (the polyline).
    def plan_route(ctx):
        def is_allowed(idx, grid):
            x, y, z = grid.coord_from_index(idx)
            return not (2.0 < x < 3.0)          # keep-out corridor
        from ada.topology.routing import route_system
        poly = route_system(ctx.system, ctx.grid, rules=RoutingRules(is_allowed=is_allowed))
        return RoutePlan(system=ctx.system, polyline=poly)

    # A modelling rule for the detail geometry at each crossing: a short sleeve
    # centred on the crossing point, along the crossed face normal.
    def model_penetration(pen, name):
        import ada
        p1 = tuple(pen.point - pen.normal * 0.15)
        p2 = tuple(pen.point + pen.normal * 0.15)
        return ada.Part(name) / ada.PrimCyl(f"{name}_sleeve", p1, p2, r=0.1)

    rules = DesignRules(plan_route=plan_route, model_penetration=model_penetration)
    result = run_design(systems, cell_graph=cg, grid=grid, rules=rules)

    # Or reuse the reference detail standard (pipe sleeve / cable block / duct frame):
    result = run_design(systems, cell_graph=cg, grid=grid, rules=standard_design_rules())

The same ruleset threads into the higher-level entry points:
:class:`~ada.topology.routing.RoutingBlueprintBase(design_rules=...)` for
blueprint-driven routing, and ``compile_procedural_doc(..., design_rules=...)``
for the viewer's compile (which defaults to ``standard_design_rules()``). With
``skip_failed=True`` a run that can't be planned is dropped and named in
``result.skipped`` rather than sinking the whole model. The legacy
subclass scaffolds (:class:`~ada.topology.routing.RoutingBlueprintBase`'s
``rules_for``/``build_routing_grid`` overrides and
:class:`~ada.topo_model.penetration.PenetrationBlueprintBase`'s
``build_penetration``) still work unchanged — the defaults simply wrap them.

Named rulesets for the viewer
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A JSON document can't carry Python callables, so the viewer selects a ruleset
**by name**. ``ada.topo_model`` keeps a small registry
(:data:`~ada.topo_model.design_rulesets.DESIGN_RULESETS`) mapping a slug to a
``DesignRules`` factory:

* ``standard`` — route runs and add the standard penetration detail at each wall
  crossing (pipe sleeve / cable block / duct frame, with the wall plate cut).
* ``route_only`` — route runs and detect crossings, but emit no detail geometry.

A cell-model document names one via ``doc["design_rules"]``, and
``compile_procedural_doc`` resolves it with
:func:`~ada.topo_model.resolve_design_rules` (an unknown/absent slug falls back
to ``standard``):

.. code-block:: python

    from ada.topo_model import resolve_design_rules, design_ruleset_specs

    rules = resolve_design_rules("route_only")          # -> DesignRules | None
    design_ruleset_specs()                               # [{slug, name, description}, ...]

The hosted viewer wires this end-to-end: workers advertise
``design_ruleset_specs()``, the API serves the built-in rulesets ∪ the advertised
ones at ``GET /procedural-models/design-rulesets``, and the cellbuilder's
**Design rules** dropdown writes the chosen slug into ``doc.design_rules`` so the
compile worker applies it. Register your own ruleset by adding a slug →
``DesignRules`` factory to ``DESIGN_RULESETS``; it then appears in the dropdown
of any scope served by a worker that ships it.

The missing-I/O report
----------------------

Every port left unconnected is a hole in the design. The validation helpers
walk a part tree and report them:

.. code-block:: python

    from ada.api.systems import find_unconnected_ports, format_port_report

    print(format_port_report(find_unconnected_ports(a)))

.. code-block:: text

    Equipment  Port     Category  Direction
    ---------  -------  --------  ---------
    Pump1      suction  process   IN
    Pump1      signal   signal    INOUT

IFC export
----------

Equipment and systems export as proper IFC4 distribution entities: the
equipment element class follows ``Equipment.ifc_element_class`` (the demo pump
is an ``IfcPump``, the tank an ``IfcTank``), ports become nested
``IfcDistributionPort`` entities with mapped flow directions, each system is an
``IfcDistributionSystem`` (typed ``WATERSUPPLY``/``ELECTRICAL``/…) grouping its
routed segments and connected equipment, and cable/duct runs emit
``IfcCableSegment``/``IfcDuctSegment`` instead of pipe segments.

Building and viewing the demo
-----------------------------

.. code-block:: bash

    pixi run -e prod topo-model-demo

The task builds the model, prints the missing-I/O report, exports a GLB,
uploads it to your personal viewer scope (when ``ADAPY_BASE_URL`` /
``ADAPY_API_TOKEN`` are configured in ``.env``; skipped otherwise) and streams
the scene to the websocket viewer via ``assembly.show()``. Use ``--no-upload``
/ ``--no-show`` to opt out of either side effect.

Compiling a whole model with ``ProceduralBuilder``
--------------------------------------------------

The engine-in-a-nutshell example above builds a bare structure. A full
procedural *model* — spaces plus equipment, routed systems, openings and a
design ruleset — is compiled by
:class:`~ada.topo_model.builder.ProceduralBuilder`, the **root object that owns
the whole model**. It is *object-first*: you hand it explicit, validated entity
objects (:class:`~ada.topology.entities.TopoSpace` /
:class:`~ada.topology.entities.TopoEquipment` /
:class:`~ada.topology.entities.TopoSystem` /
:class:`~ada.topology.entities.TopoOpening`) rather than a loose dict, and
``compile()`` returns GLB bytes:

.. code-block:: python

    from ada.topo_model import ProceduralBuilder
    from ada.topology.entities import TopoSpace, TopoEquipment, TopoSystem

    spaces = [
        TopoSpace(NAME="Cell1", X=0, Y=0, Z=0, DX=5, DY=5, DZ=3),
        TopoSpace(NAME="Cell2", X=5, Y=0, Z=0, DX=5, DY=5, DZ=3),
    ]
    equipment = [
        TopoEquipment(NAME="Pump2", DESCRIPTION="pump", SPACE_NAME="Cell1",
                      SPACE_LOC="FLOOR", X=2, Y=2, Z=0, LX=1, LY=1, LZ=1,
                      COGx=0, COGy=0, COGz=0.5, massDry=1000, massCont=0),
        TopoEquipment(NAME="Tank2", DESCRIPTION="tank", SPACE_NAME="Cell2",
                      SPACE_LOC="FLOOR", X=6.5, Y=1.5, Z=0, LX=2, LY=2, LZ=2,
                      COGx=0, COGy=0, COGz=1.0, massDry=1000, massCont=0),
    ]
    systems = [
        TopoSystem(NAME="CoolingWater", TYPE="piping", MEDIUM="water", CONNECTIONS=[
            {"EQUIPMENT": "Pump2", "PORT": "discharge"},
            {"EQUIPMENT": "Tank2", "PORT": "inlet"},
        ]),
    ]
    glb_bytes = ProceduralBuilder(spaces=spaces, equipments=equipment, systems=systems).compile()

``compile()`` runs the phases in order — ``build_structure`` →
``build_equipment`` → ``build_systems`` → ``to_glb``. Drive them individually to
inspect the owned state (``blueprint``, ``cell_graph``, ``equipment_map``,
``systems_parts``, ``assembly``) in between:

.. code-block:: python

    pb = ProceduralBuilder(spaces=spaces, equipments=equipment, systems=systems)
    pb.build_structure()
    print(pb.cell_graph.get_external_floors())    # the built topology
    pb.build_equipment()
    print(pb.equipment_map)                       # {"Pump2": <ada.Equipment>, ...}
    pb.build_systems()
    glb_bytes = pb.to_glb()

Every child reaches the root through an injected ``.procedural`` reference — the
blueprint directly, and any ``GraphFace`` through its cell graph — so a blueprint
or a face-level rule can consult the whole model (equipment, systems, the design
ruleset, the LOD). The topology engine
(:class:`~ada.topology.TopologyBuilder`) is reached the other way, as
``pb.topology``; the LOD lives once on the root (``pb.detail``):

.. code-block:: python

    pb.blueprint.procedural is pb                              # True
    face = pb.cell_graph.get_external_floors()[0]
    face.parent_cell.cell_graph.procedural is pb              # True

Loading from dict / JSON / Excel
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Explicit objects are the sturdy path, but a model also loads from the three
document formats — each parses and validates into those same entity objects, so
dict parsing lives in exactly one place:

.. code-block:: python

    # a procedural document (the viewer's commit format) — dict or a JSON file
    pb = ProceduralBuilder.from_dict(doc)
    pb = ProceduralBuilder.from_json("model.json")
    pb = ProceduralBuilder.from_json('{"spaces": [...], "systems": [...]}')

    # a multi-sheet workbook: Spaces / Equipments / Openings / Systems + a
    # vertical Model sheet (name, blueprint, blueprint options, design ruleset)
    pb = ProceduralBuilder.from_excel("model.xlsx")

    # and the inverse — round-trips the whole model back out
    pb.to_json("model.json")
    pb.to_excel("model.xlsx")

The functional ``compile_procedural_doc(doc, ...)`` is a thin wrapper over
``from_dict`` + ``compile`` — use the builder when you want the phases, the
intermediate model, or the object/Excel round-trips; the function for a one-shot
document compile.

Multiple structures in one model
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A single document (or workbook) can carry **several** topology models —
*structures* — each a named group of spaces/openings placed at its own origin. A
``Structures`` sheet (:class:`~ada.topology.entities.TopoStructure`: ``NAME`` +
``X``/``Y``/``Z``) lists them, and every entity is tagged with its
``STRUCTURE_NAME``:

.. code-block:: python

    doc = {
        "structures": [
            {"NAME": "Deck_A", "X": 0, "Y": 0, "Z": 0},
            {"NAME": "Deck_B", "X": 20, "Y": 0, "Z": 0},
        ],
        "spaces": [
            {"NAME": "A1", "STRUCTURE_NAME": "Deck_A", "X": 0, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3},
            {"NAME": "B1", "STRUCTURE_NAME": "Deck_B", "X": 0, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3},
        ],
    }
    glb_bytes = ProceduralBuilder.from_dict(doc).compile()

The same ``ProceduralBuilder`` builds one topology model per structure (grouped by
``STRUCTURE_NAME``) and places each at its origin — no separate multi-builder.
Equipment and systems stay a **single shared layer** (not duplicated per
structure). With no ``structures`` the whole document is one implicit model, so a
plain single-structure build is unchanged. The ``Structures`` sheet + entity
``STRUCTURE_NAME`` mirror the sibling procedural-modelling tool's workbook, so a
model round-trips between the two.

Reading the catalog from Python
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When the viewer's postgres is reachable, :class:`~ada.topo_model.catalog.ProceduralCatalog`
reads a scope's reusable equipment types and system templates and turns them
into the objects above. ``equipment_resolver()`` returns the ``slug -> catalog
doc`` callable the builder uses to expand a placed catalog equipment (referenced
by its ``DESCRIPTION`` slug) into a full :class:`ada.Equipment` — ports and IFC
class included:

.. code-block:: python

    from ada.topo_model import ProceduralBuilder, ProceduralCatalog

    with ProceduralCatalog.connect(scope_kind="user", scope_id="me") as cat:
        for et in cat.list_equipment_types():
            print(et.slug, et.name, et.doc["ifc_element_class"])
        for st in cat.list_system_templates():
            print(st.slug, st.doc["type"], st.doc.get("medium"))

        # instantiate directly from a catalog type…
        pump = cat.get_equipment_type("pump").to_equipment("P1", origin=(2, 2, 3))
        cw = cat.get_system_template("cooling_water").to_system(
            "CW", connections=[{"EQUIPMENT": "P1", "PORT": "discharge"}])

        # …or let the builder resolve placed catalog slugs at compile time
        pb = ProceduralBuilder(spaces=spaces, equipments=equipment, systems=systems,
                               equipment_resolver=cat.equipment_resolver())
        glb_bytes = pb.compile()

``connect`` defaults ``database_url`` to the ``DATABASE_URL`` environment
variable and binds the reader to one scope for its lifetime; use it as a context
manager (or call ``close()``) to release the connection pool.

Viewer catalogs: equipment types and system templates
------------------------------------------------------

The catalogs above are edited in the hosted viewer's admin panels and mirror the
Python API — the same equipment-type / system-template rows feed both.

The hosted viewer exposes two per-scope catalogs that feed the cellbuilder,
backed by postgres (migrations ``023``/``024``) and edited from admin panels:

* **Equipment types** — reusable archetypes with a name/description/slug, a
  bounding box, mass, IFC element class and a port/nozzle list (each port a
  local position + outward direction, tagged process/electrical/signal). A CAD
  asset can be attached (uploaded or copied from a scope file); a worker
  ``equipment_bbox`` job then infers the bounding box and renders a preview GLB.
  Placed catalog equipment resolve by slug at compile time
  (``compile_procedural_doc(..., equipment_resolver=...)``) into a full
  :class:`ada.Equipment` — ports and IFC class included.

* **System templates** — named service systems (category/type, medium, voltage,
  pipe radius/wall thickness) that seed the cellbuilder's systems inspector.

When a compiled model enables *"use CAD models for equipment"*
(``doc["equipment_cad"]``), catalog equipment that have a linked CAD asset are
built without their placeholder box and the real CAD geometry is spliced into
the output GLB at the cell footprint
(``compile_procedural_doc(..., cad_scene_resolver=...)``).
