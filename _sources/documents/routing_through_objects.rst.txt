Routing through things: in-line components and branches
========================================================

.. note::
   **Status.** Stage 0 (the local viewer's ``file://`` origin) and Stage 2 (branches) have landed;
   see the status note at the top of each section below for what shipped and what's still a known
   gap. Stage 1 (waypoints -- routing through an in-line component) and Stage 3 (a real in-line tee)
   are still plans, nothing in them implemented -- though Stage 3's two load-bearing design
   decisions have been taken and are recorded in its section. Delete this document, or fold what
   survives into :doc:`dexpi` and the routing docs, once those two land too.

   Stage 2 is what makes Stage 3 worth reading: it gave a branch the right topology and explicitly
   not the right geometry, so "a branch" today is three pipes that never touch.

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

.. note::
   **Landed.** ``System.segments`` (already existed, for round-trip detail) is the branched form
   this doc asked to design: two or more segments whose ports share a common junction *equipment*
   (not one shared port -- a tee's three legs each get their own dedicated port on it, from
   :func:`~ada.cadit.dexpi.equipment_list.branch_points` /
   :func:`~ada.cadit.dexpi.read.to_procedural._junction_equipment`, unchanged) turn a ``System``
   into a branch. :func:`ada.topology.routing.route_system` detects that shape and dispatches to
   :func:`~ada.topology.routing.route_branched_system`: every leg already has two fully-resolved
   ports, so each routes as an ordinary two-port run (the multi-goal "route to nearest point on the
   network" search this doc originally called for turned out to be unnecessary -- the junction
   equipment already anchors where every leg ends). The two legs with the farthest-apart leaf ports
   become the trunk; :func:`~ada.topology.routing.system_route_to_geometry` emits one swept run per
   leg plus a small hub solid (a sphere sized to the run's cross-section) at the junction, in place
   of the plain ``IfcPipeFitting`` box. The DEXPI importer
   (:func:`~ada.cadit.dexpi.read.to_procedural._fold_branch_groups`) folds a 3+-way junction's
   segments into one such branched system (a 2-way junction stays two two-ended systems -- Stage 1
   territory, unaffected); the merge writer
   (:mod:`ada.cadit.dexpi.write.from_ada`, ``_branch_legs``/``_sync_segment_connections``) splits it
   back into the source's original ``PipingNetworkSegment``\ s by the per-leg name the importer
   stashed, so an unedited round-trip is a no-op exactly as it was before branches existed. A
   :class:`~ada.api.systems.base.System` can also be built as a branch directly, via
   ``System.add_leg(name, start, end)`` (each end a :class:`Port` or an ``(equipment, port_name)``
   pair).

   **What this did not do.** Branch routing supports exactly **one** junction per system -- two
   adjacent 3+-way junctions joined by a bare segment (no equipment between two tees) are left
   unfolded, same as before this landed (see :func:`_fold_branch_groups`'s docstring). The junction
   fitting is a placeholder hub, not a sized reducing-tee shape (bevels, face-to-face length, a
   differently-sized branch outlet) -- deliberately out of scope, same spirit as Stage 1's own
   fitting-geometry non-goal below. Cross-system clash avoidance and wall-penetration planning cover
   every branch leg (not just the trunk) for occupancy, but nothing here changes penetration
   *detail* modelling for a leg that crosses a wall.

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
   that is one ``System`` with a tree of segments, or a ``System`` composed of ``SystemSegment``\ s
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
``to_dexpi`` round-trips it back to the same number of ``PipingNetworkSegment``\ s the source had.

Stage 3 -- a real tee: three runs meeting on one centreline
-------------------------------------------------------------

.. note::
   A **plan**. Nothing in this stage is implemented. Two design decisions have been made and are
   recorded below (the authoring API, and internal-volume correctness); everything else is open.

Stage 2 gave a branch the right *topology* -- one ``System``, three legs, each resolving to its own
port, round-tripping to the source's three ``PipingNetworkSegment``\ s. It did not give it the right
*geometry*. What a Stage 2 branch actually produces is three separate :class:`ada.Pipe` objects
ending at three scattered port positions on a small equipment box, plus a placeholder sphere
spanning them. The pipes never touch.

That is not how process piping works. A tee is an **in-line fitting** -- part of the run, welded or
flanged in -- and the branch centreline meets the header centreline at a point. DEXPI agrees: a
``PipeTee`` is a ``PipingComponent``, the same category as a valve. Plant tools (E3D/PDMS, SP3D,
Plant 3D) place a tee as a catalog component sitting in the run. Modelling it as a standalone
equipment box is an adapy-side convenience, not a statement about the plant.

**The decision this stage rests on -- and it is closer than a first reading suggests.** A tee could
be added *without* touching :class:`ada.Pipe` at all: a standalone fitting object placed where three
separate pipes meet, with real tee geometry and centreline-accurate placement. That is roughly a
fifth of the work and produces geometry that looks and exports correctly.

The argument first made against it was that only a branch-aware :class:`ada.Pipe` lets the model
*assert* the three legs are one run. **That argument was too strong, because a container for
exactly that already exists.** :class:`~ada.api.systems.base.PipingSystem` holds all three legs in
``route_geometry``, survives onto ``Assembly.systems``, and maps to an ``IfcDistributionSystem`` on
export -- which is precisely IFC's own way of saying "these flow elements are one run". So the
grouping claim is already representable, and after the fix recorded below it is actually made.

What a branch-aware ``Pipe`` still buys, stated honestly and no wider:

* ``pipe.segments`` is *itself* a connected run, so anything walking a single pipe -- the take-off's
  per-pipe mass, the clash check, a future centreline query -- sees the branch without having to
  know about ``System``. Today those consumers see three unrelated pipes and a loose fitting, and
  only ``System`` knows better.
* One pipe means one ``IfcDistributionSystem`` by construction rather than by a merge step.

That is a real but *narrower* benefit than "only this can assert it". A reader deciding between the
two options should weigh it against roughly five times the work, and should know that the cheaper
option composes with the routing that already exists.

.. note::
   **A bug this analysis found, now fixed.** ``_resolve_distribution_system``
   (``cadit/ifc/write/write_equipment.py``) took *the first* ``IfcDistributionSystem`` among a
   system's route geometry and folded the system's name, predefined type and equipment membership
   onto it. That was right while one ``System`` meant one ``Pipe``. Stage 2 made a branched system
   hold one Pipe **per leg**, each of which writes its own group -- so the fixture exported **nine**
   distribution systems where there are five logical ones, with two legs per branch left under their
   pipe-derived names and a ``NOTDEFINED`` predefined type, i.e. asserting the legs are unrelated
   runs. The groups are now merged so one logical system is one ``IfcDistributionSystem``; the
   regression test is ``test_a_branched_system_exports_as_one_distribution_system``.

What the survey found (and what it corrected)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The chain assumption is far more contained than it looks, and an earlier reading of this that called
it "load-bearing across IFC, the viewer and FEM" was simply wrong. Everything past
``pipe.segments`` already treats the list as a flat bag of independently-geometried objects:

* **IFC export** flat-loops the segments and groups them in an ``IfcDistributionSystem``
  (``cadit/ifc/write/write_pipe.py``). No ``IfcDistributionPort``, no ordering, no connectivity
  between consecutive segments anywhere in the pipe write path -- ports are an equipment-only
  concept in this codebase (``write_equipment.py``).
* **Tessellation and the viewer tree** add one graph node per segment and tessellate each from its
  own ``solid_geom()`` (``visit/scene_from_object.py``). Nothing aggregates a pipe into one run.
  Segment names are positional only in the name; nothing parses the ordinal back.
* **FEM** flat-loops the segments (``fem/meshing/concepts.py``).

The linearity lives in essentially one function: ``segments3d_from_points3d``
(``core/curve_utils.py``), which walks the flat point list pairwise and fillets consecutive pairs.
Its "no shared point found" check *logs* rather than raises, which is worth knowing before relying
on it as a guard.

**There is already a precedent for the shape this stage needs.** The IFC reader builds a
:class:`ada.Pipe` whose segments are assigned directly and whose ``points`` are a degenerate
two-point placeholder, precisely so a re-import does not rebuild (and flatten) the real
straight/elbow decomposition -- see ``cadit/ifc/read/read_ifc.py``. A ``Pipe`` whose segments are
not derived from its points is therefore an already-supported, already-exercised state, not a new
concept this stage invents.

**No tree structure is needed, and none should be introduced.** The segment list is already a bag;
a tee is a bag element that happens to have three ends, and the topology lives in the segments'
shared endpoints exactly as it implicitly does today. There is no tree-shaped ``BackendGeom``
anywhere in adapy and this stage must not create the first one.

Decision 1 -- the authoring API
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``Pipe(name, points, sec)`` takes one polyline and has nowhere to say "a branch leaves at X toward
Y". Two options were considered: let a caller supply segments directly (following the reader
precedent), or accept a **branch spec alongside** ``points``, with ``points`` remaining the trunk
centreline.

**Decided: the branch spec.** ``points`` stays the trunk; a branch is declared against it. The
direct-segments route stays available -- it already exists for the reader -- but it is the
machine-facing path, not the one a person writing a model should have to use. Authoring ergonomics
win because this API is the thing a user actually types:

.. code-block:: python

   pipe = ada.Pipe("L-301", [(0, 0, 0), (6, 0, 0)], "PIPE200",
                   branches=[ada.PipeBranch(at=(3, 0, 0), to=(3, 4, 0))])

The exact spelling is open (``at``/``to`` versus a point pair, whether a branch may itself carry a
polyline rather than a single leg endpoint, whether ``branches`` takes its own section for a
reducing tee). What is settled is that the trunk is expressed as it is today and a branch is
additive, so **every existing** ``Pipe`` **call site is unchanged** -- that is the compatibility
contract, and it should be asserted rather than assumed.

*The hard part, honestly.* ``at`` must lie on the trunk centreline. A caller will eventually pass a
point that is merely near it, and the failure has to be legible -- snap to the nearest point on the
polyline within a tolerance and say so, or refuse and name the offending point and its distance.
Silently teeing off a point that is not on the run is the one outcome to design against.

Decision 2 -- the tee body, and internal-volume correctness
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A tee body is naturally the union of a run and a branch. The cheap version unions two *annular*
profiles -- the profile ``section_to_arbitrary_profile_def_with_voids`` already produces for a
``TUBULAR`` section, an outer circle with the bore as an inner void. That is wrong in a way a viewer
cannot show you: the branch's inner wall goes on crossing the run's bore, so the internal partition
between them is never removed. The solid looks perfect from outside and its internal volume is
nonsense.

**Decided: internal-volume correctness is required.** The tee is therefore

.. math::

   (\text{outer}_\text{run} \cup \text{outer}_\text{branch}) \setminus
   (\text{bore}_\text{run} \cup \text{bore}_\text{branch})

**This needs no new geometry-layer machinery.** ``apply_geom_booleans``
(``occ/geom/boolean.py``) is a sequential left-fold over the operation list, so an ordered flat list
expresses the nesting directly -- :math:`(A \setminus B) \setminus C = A \setminus (B \cup C)`,
so the two bores can be cut one after the other:

.. code-block:: text

   base = outer_run                       (extruded SOLID disc, radius r)
   ops  = [UNION      outer_branch,       (extruded SOLID disc, radius r)
           DIFFERENCE bore_run,           (extruded SOLID disc, radius r - wt)
           DIFFERENCE bore_branch]        (extruded SOLID disc, radius r - wt)

The one thing this changes about how pipe geometry is built: the four operands are **solid discs**,
not the annulus the existing helper returns. They are ``ArbitraryProfileDef``\ s over a single
``Circle`` with no inner curve, built inline. Reusing the annular profile here is the specific
mistake this decision exists to prevent.

*Mirror the degenerate-bore guard.* ``section_to_arbitrary_profile_def_with_voids`` drops the inner
circle when ``r - wt`` falls below 1 µm, because a near-zero circle is a degenerate edge that aborts
the solid build downstream. A tee over a solid-bar section must skip both DIFFERENCE operations for
the same reason, rather than cutting with a degenerate disc.

What else has to change
~~~~~~~~~~~~~~~~~~~~~~~~~

1. ``PipeSegTee`` -- a new segment type beside ``PipeSegStraight``/``PipeSegElbow``, with
   ``solid_geom``/``solid_occ``/``shell_occ`` like its siblings. Constructor mirrors the elbow's
   shape: the run axis plus the branch endpoint, with the junction implied at the branch's foot on
   the run axis.
2. A branch-aware path in ``build_pipe_segments_alt``: split the trunk at each branch point, emit
   the tee, and emit the branch leg's own straights and elbows.
3. **IFC write** -- one arm in ``write_pipe_segment``, which is currently a two-way ``isinstance``
   that raises on anything else. ``fitting_entity_class`` already yields ``IfcPipeFitting``; elbows
   set ``PredefinedType="BEND"``, so a tee sets ``"JUNCTION"`` (a valid IFC4
   ``IfcPipeFittingTypeEnum``).
4. **IFC read** -- one arm in the reader's dispatch, which today is binary: *everything that is not
   an* ``IfcPipeSegment`` *is an elbow*. A tee currently falls into ``read_pipe_elbow``, throws on
   the unexpected axis polyline, and is swallowed by the reader's per-segment ``except Exception``.
   **So the present behaviour for any three-ended fitting is to silently drop it on re-import** --
   worth fixing regardless of the rest of this stage.
5. Four ``isinstance``-tuple additions so the new type is not silently skipped: ``consolidate_materials``
   (``part.py``), ``reader_utils`` (which raises ``NotImplementedError`` on an unknown segment),
   ``takeoff`` (which ``continue``\ s, so a tee would contribute zero mass), and ``clash_check``
   (which filters to ``PipeSegStraight``, so a tee would go unchecked).

**Definition of done.** A ``Pipe`` authored with a branch produces one connected run whose geometry
is checkable rather than merely reported: the tee solid's internal volume equals the union of the
three bores (not the union minus an internal partition), the branch leg's centreline terminates
exactly on the trunk centreline, the take-off counts the tee's mass once, and an IFC round-trip
returns the same segment count and the same three ends -- with the tee still a tee, not dropped.

What Stage 3 deliberately does not attempt
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **A catalog tee.** Real tees come from a spec with a face-to-face length, a crotch radius, a
  reinforcing pad or a weldolet. This stage models a bored intersection of two cylinders, which is
  the geometry, not the product.
* **Reducing tees, laterals and wyes.** The branch shares the run's section and meets it
  perpendicular. A different branch diameter is a plausible next increment (the ``branches``
  argument is shaped to allow it); a non-perpendicular lateral is a different geometry problem.
* **Re-routing to create branches.** This stage models a branch a caller declares. Deciding *where*
  a branch should tee off an existing header is Stage 2's routing question, and it is not revisited
  here.
* **Migrating the DEXPI junction path onto it.** The importer's materialised junction equipment
  keeps working exactly as Stage 2 left it. Moving DEXPI branches onto real tees is a follow-on that
  should happen only once a hand-authored tee is proven, because it changes what a P&ID import
  produces.

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

.. note::
   **Landed, with one item unconfirmed.** The ``/config.js``/``/favicon.svg`` 404 is fixed at the
   source: ``RendererReact._extract_html`` now strips both tags from the file on disk right after
   unzipping the bundle, so plain ``show()`` (which opens that file directly) never requests them --
   previously the strip only ran in ``get_html_with_injected_data``, the REST-embedded path.
   The absolute-path leak is fixed at the DEXPI-to-procedural-document boundary: ``ResolvedDexpi.source``
   (:func:`~ada.cadit.dexpi.read.to_procedural.dexpi_to_resolved`) is now the source file's
   **basename**, not ``DexpiDocument.source``'s full path -- which still has to stay a real path
   internally (:class:`~ada.cadit.dexpi.store.DexpiStore` reads it back to reopen/save the file), so
   the fix reduces it to a basename only at the point it crosses into the browser-facing document,
   not upstream. The "frame load refused as a unique security origin" item was investigated and
   could not be reproduced against the current tree -- no iframe touches the procedural-panel data
   path (the one that used to exist, an HTTP fetch for the procedural document, was already replaced
   by the GLB-embedded ``procedural_doc`` before this pass started); it may already be moot, but
   nobody has confirmed that against a real browser console, so treat it as open until someone does.

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
  swept solid. Stage 3 is the one exception and is deliberately narrow: it models a *tee*, because a
  branch is not representable at all without one, and it models it as a bored cylinder intersection
  rather than as a catalog product.
* **Re-ordering or optimising the process.** The P&ID's component order and its branch topology are
  statements about the plant, and the router's job is to realise them, not to improve them.
