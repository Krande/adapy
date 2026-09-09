Routing through things: in-line components and branches
========================================================

.. note::
   A **plan**, not a description of what exists. Nothing here is implemented yet. Delete this
   document, or fold what survives into :doc:`dexpi` and the routing docs, once the work lands.

Two gaps in :mod:`ada.topology.routing` look different from the outside and are the same thing
underneath. A routed run today is a swept solid between exactly two ports, produced by A* over a
grid, and it has **no concept of anything along its length**:

*In-line components are placed but not passed through.*
    A DEXPI segment carries the valves, strainers, reducers and orifices the line runs through.
    ``inline_components="equipment"`` materialises each as its own small ``ada.Equipment`` with real
    ports -- and then the layout drops it *somewhere on a deck*, because the router has nowhere to
    put it. On the ``unit_separator_proteus.xml`` fixture that is eleven bodies (``HV-201``,
    ``HV-203A/B``, ``HV-206``, ``NRV-204A/B``, ``PV-202``, ``RO-202``, ``RE-204``, ``ST-201``, and
    the actuator ``PV-202.01``) standing unconnected to anything. The model is not wrong so much as
    it is visibly unfinished: the P&ID says these sit *in* the line, and the 3D model says they sit
    near it.

*A branch is three runs meeting at a box.*
    ``route_system`` routes exactly ``ports[0] -> ports[-1]``. A ``PipeTee`` is therefore
    materialised as a small ``IfcPipeFitting`` and the segments that meet there become separate
    two-ended runs into it. Each one routes; no single ``System`` spans the junction; and there is
    no branched pipe geometry anywhere in the model. Across the official corpus that is 52 three-way
    junctions in 21 of 220 files (plus 37 two-way ones, which are pass-throughs rather than
    branches -- see below).

Both are "the run cannot acknowledge an object on it". Fixing them separately would mean building
the same machinery twice, which is the argument for planning them together even if they land
apart.

What exists to build on
------------------------

* :func:`ada.topology.routing.astar_route` and ``astar_route_constrained`` -- 6-connected orthogonal
  A* over a :class:`~ada.topology.grid.CellGrid`, with pluggable per-move rules.
* :func:`~ada.topology.routing.route_system` -- wires ``ports[0] -> ports[-1]``, then
  ``system_route_to_geometry`` sweeps the polyline.
* :func:`ada.cadit.dexpi.equipment_list.branch_points` -- already identifies which fittings more than
  one segment meets at, and is already shared by the importer and the merge writer.
* The importer already records every in-line component on the run's metadata
  (``_component_metadata``: DEXPI id, class, tag, nominal diameter, piping class), in **segment
  order**, whether or not it was materialised as equipment.

So the connectivity and the ordering are known. What is missing is the routing.

Stage 1 -- waypoints: run *through* an in-line component
----------------------------------------------------------

The smaller of the two, and it clears eleven of the twelve floating bodies in the fixture.

**The idea.** A run gets an ordered list of intermediate ports it must pass through. Instead of one
A* call from start to end, route ``start -> w1 -> w2 -> ... -> end`` and concatenate. Each leg is
the existing solver, unchanged.

**Where it goes.**

1. ``route_system`` grows an optional ``waypoints`` argument (ordered ports). Absent, behaviour is
   byte-identical to today -- that is the compatibility contract, and it should be asserted rather
   than assumed.
2. ``ada.topo_model.compile._wire_systems`` learns to read the ordered in-line components off the
   spec and resolve each to the placed equipment's ports.
3. The importer stops treating a materialised in-line component as free-standing and instead
   contributes it to its run's waypoint list.

**The hard parts, honestly.**

*Which port, and in which order.* A valve has two ports; the run must enter one and leave the other.
Entering and leaving the same port is a degenerate route that A* will happily produce. Direction has
to come from the segment's flow ordering, not be guessed geometrically.

*The layout has to cooperate.* A waypoint is only routable if the component sits somewhere the run
can reach. Today shelf packing places it with no idea it is on a line. The minimum is to group each
in-line component with its run (partly done -- ``LayoutItem.group``); the honest version places it
*along* the corridor between the run's two ends, which is a layout feature, not a routing one.
**Expect this to be the real work**, and expect the first attempt to produce runs that zig-zag
across a deck to visit a valve that was dropped in the wrong place.

*Failure has to stay legible.* If one leg fails, the whole run fails. The report must say which
leg and which component, or a user sees "no route found" for a run that was routing fine yesterday.

*Do not silently reorder.* If the waypoint order the P&ID gives produces a worse route than some
other order, that is not licence to permute it: the order is process meaning, not a hint.

**Definition of done.** On the fixture with ``inline_components="equipment"``, every materialised
valve has its run's pipe passing through both its ports, verified by checking the routed polyline
touches each waypoint position -- not by checking the run "reports" as connected. The distinction
matters and has bitten this codebase before (see the branch-point note in
``dexpi_branch_status.rst``: a wiring-only assertion passes happily when three runs resolve to one
port and three pipes converge on a single point).

Stage 2 -- branches: one system spanning a junction
-----------------------------------------------------

**First, size it honestly.** Of the 89 branch points in the corpus, 37 have degree two. Those are
not branches at all -- they are a run split into two segments at a component, and **stage 1 solves
them**: the component becomes a waypoint and the two segments become one run through it. Only the
52 of degree three need real branch support. Doing stage 1 first therefore shrinks stage 2's
remaining scope by more than a third, which is the main argument for the ordering.

**The idea.** A branched system is a tree, not a path. Route the trunk first (the two ports furthest
apart, or the two the P&ID marks as the main line), then route each remaining branch from its port
to the *nearest point already on the trunk* rather than to a port. That is a Steiner-tree
approximation, and the cheap version of it is well understood: it is what "route to the existing
network" means in every pipe router.

**What has to change.**

1. ``System`` gains a branched form. Today ``system.ports`` is a flat list and the geometry is one
   swept solid; a branched run is several solids plus a fitting at each junction. Deciding whether
   that is one ``System`` with a tree of segments, or a ``System`` composed of ``SystemSegment``\\ s
   (which already exist), is the first design decision and it should be made before any routing
   code is written.
2. A* needs a "route to any node in this set" goal, not "route to this node". The existing solver
   takes a single goal index; the multi-goal variant is a small change to the frontier's termination
   test, not a new algorithm.
3. The junction needs real geometry -- a tee fitting sized to the two diameters -- where today
   there is an ``IfcPipeFitting`` box. This is where the branch stops being a routing problem and
   becomes a detailing one, and it may belong with the existing fitting/detailing machinery rather
   than in the router.
4. The DEXPI importer stops emitting one system per segment for segments that meet at a junction,
   and emits one branched system instead. **This is a behaviour change with a blast radius**: the
   system count drops, names change, and ``to_dexpi`` has to split a branched system back into the
   segments the source had. The merge writer's ``_segment_boundary`` already knows about junctions;
   it will need to know about this too.

**The trap that is already documented and will still be true.** The segment that *nests* a tee in
the XML has no more claim to route through it than the segments referencing it from outside. That
asymmetry has bitten once already (``dexpi_branch_status.rst``); a branched-system importer must not
reintroduce it by treating the nesting segment as the trunk by default.

**Definition of done.** A three-way junction in the fixture produces **one** ``System`` whose
geometry is a connected tree, with the branch meeting the trunk at a modelled fitting, and
``to_dexpi`` round-trips it back to the same number of ``PipingNetworkSegment``\\ s the source had.

Order, and what to do if only one gets done
---------------------------------------------

Stage 1 first, for three reasons: it is smaller, it removes 37 of the 89 junctions from stage 2's
scope, and the multi-leg routing it introduces is the same machinery stage 2 needs for branch legs.

If only stage 1 lands, the model is honestly better -- valves sit in their lines -- and the branch
gap is exactly where it is today, documented and understood. If only stage 2 lands, eleven bodies
still float and the tee looks solved while the line through it does not. That asymmetry is the
argument for the order.

Stage 0 -- the local viewer's ``file://`` origin
--------------------------------------------------

Smaller than either stage above and independent of both, but it is what a user meets first, and one
part of it is a privacy problem rather than a cosmetic one.

``assembly.show()`` opens the extracted ``index.html`` directly over ``file://`` and streams the
scene over the websocket. Three things go wrong there, all visible in the browser console:

*The page requests ``/config.js`` and ``/favicon.svg`` and both 404.* Those are absolute paths that
only exist when the REST app serves the SPA over HTTP. :meth:`RendererReact.get_html_with_injected_data`
already strips both tags for exactly this reason -- but that is the *embedded* path, and plain
``show()`` serves the raw extracted file, which still carries them. The fix is to strip them on
extraction (or serve a stub), so the two paths agree.

*A frame load is refused as a unique security origin.* ``file:`` URLs are each their own origin, so
anything the page loads into a frame from its own directory is cross-origin. Whatever needs that
frame does not work locally today.

*The refusal message embeds the absolute path of the file on disk* -- the user's home or checkout
directory, printed into the console and into any log or screenshot taken of it. **That is the part
worth fixing on its own merits**, independently of whether the frame is needed: a viewer should not
be naming the machine it is running on. Anything that ends up in a console message, a page title, a
window name or a serialized error should carry the *file name* or a stable model identifier, never
the full path it was loaded from.

Work item: audit the local ``show()`` path for absolute filesystem paths reaching the page --
``local_html_path.as_uri()``, source names threaded into ``setLoadedSourceName``, anything stamped
into the GLB's ``asset.extras`` or ``ADA_EXT_data`` -- and reduce each to a basename or an id. There
is a check for the analogous problem on the bundle already (``tools/check_bundle_provenance.py``
refuses a bundle containing strings that do not occur in the frontend sources, which is how a
build-label leak was caught); the runtime equivalent does not exist.

What this plan deliberately does not attempt
-----------------------------------------------

* **Routing across decks with vertical shafts.** Still deferred; a branch tree makes it more
  tempting and no more solved.
* **Fitting geometry for every in-line component.** A waypoint makes the run pass through a valve's
  ports; it does not model the valve body's bore, flanges or face-to-face length. The run is still a
  swept solid.
* **Re-ordering or optimising the process.** The P&ID's component order and its branch topology are
  statements about the plant, and the router's job is to realise them, not to improve them.
