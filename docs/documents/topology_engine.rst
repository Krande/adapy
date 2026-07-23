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
(``ada.Pipe`` for piping; a carrier pipe tagged as cable/duct for the other
categories). For blueprint-driven routing, subclass
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
