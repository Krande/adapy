"""Which region of the emitted geometry a ``Bc``'s node set resolves to -- and what is refused.

Licence-free: everything here reads the plan and the emitted text. Whether the calls are *right*
is settled by running them, in ``test_cae_licensed_acceptance.py``.

adapy has no geometry sets at concept level: a ``Bc`` is given through a ``FemSet`` of **mesh
nodes**. CAE carries a support on **geometry**, which is the whole reason to prefer it -- a region
of located mesh nodes holds exactly until the part is meshed again. So the writer classifies the
nodes against the geometry it is about to build, and this file pins each of the four outcomes.

Two measurements decide the shape of the rule and are quoted where they are used:

* a ``DisplacementBC`` on an edge region restrains **every** node CAE puts on that edge, and it
  picks up new ones on a re-mesh -- 10 reacting nodes at a 0.125 seed became 18 at 0.0625, with
  nothing between the two solves but a re-seed and a re-mesh. So a set covering *part* of an edge
  would silently become a support over all of it, and is refused;
* an edge region is found by ``getByBoundingBox``, one box per collinear run, and never by
  ``findAt``. On a strip whose ``x = 0`` boundary a stiffener has split into two 0.25 m sub-edges,
  ``findAt`` there returns **one** edge of length 0.25 -- half the support -- while one box over the
  whole run returns 2 edges totalling 0.5. And one box over the strip's *two long edges* returns 7
  edges totalling 13.0, which is the whole body: hence one box per run, and hence the edge count and
  the total length are both asserted in the kernel.

The model is the strip the cross-solver plate comparison is closed with, rebuilt here rather than
imported from ``verification/``: a unit test that needed a verification package on the path would be
a unit test with a licence-shaped dependency. Its three supports are the ones the writer used to
refuse.
"""

from __future__ import annotations

import pytest

import ada
from ada.cadit.cae.analysis import (
    EDGE_BOX_TOL,
    AnalysisNotSupported,
    classify_region,
    edge_index,
    vertex_index,
)
from ada.cadit.cae.writer import CaeWriteError, build_plan
from ada.fem import Bc, FemSet, Load, StepImplicitStatic
from ada.materials.metals import CarbonSteel

from .cae_script_graph import check_emitted_script
from .conftest import require_writer

require_writer()

LENGTH = 4.0
WIDTH = 0.5
THICKNESS = 0.010
SEED = 0.125
BAR_WIDTH = 0.010
BAR_HEIGHT = 0.045

#: The three supports of a simply supported strip in cylindrical bending, as
#: ``(set name, adapy dofs, the nodes it acts on)``. The same statement as
#: ``verification.genie_vs_abaqus.plate_model.EDGE_SUPPORTS``, which is what the licensed
#: acceptance reproduces.
SUPPORTS = (
    ("SS_X0", (1, 2, 3), "x0"),
    ("SS_X1", (2, 3), "x1"),
    ("CYL", (2, 4), "long"),
)


# ---------------------------------------------------------------------------------- the model


def strip(*, stiffened: bool = False, seed: float = SEED) -> ada.Assembly:
    """The 4.0 x 0.5 m strip, 10 mm, meshed as quadrilateral shells, with no analysis yet."""
    mat = ada.Material("S355", CarbonSteel("S355"))
    outline = [(0.0, 0.0), (LENGTH, 0.0), (LENGTH, WIDTH), (0.0, WIDTH)]
    objects: list = [ada.Plate("strip", outline, THICKNESS, mat=mat)]
    if stiffened:
        objects.append(
            ada.Beam(
                "bar",
                (0.0, WIDTH / 2, 0.0),
                (LENGTH, WIDTH / 2, 0.0),
                ada.Section("BAR", "FB", h=BAR_HEIGHT, w_top=BAR_WIDTH, w_btn=BAR_WIDTH),
                mat,
            )
        )
    part = ada.Part("Strip") / objects
    assembly = ada.Assembly("StripSite") / part
    part.fem = part.to_fem_obj(seed, "line", use_quads=True, interactive=False)
    return assembly


def nodes_where(fem, which: str) -> list:
    """The nodes one :data:`SUPPORTS` entry acts on, by position, sorted by id."""
    tol = 1e-09
    if which == "x0":
        found = [n for n in fem.nodes if abs(n.x) < tol]
    elif which == "x1":
        found = [n for n in fem.nodes if abs(n.x - LENGTH) < tol]
    elif which == "long":
        found = [n for n in fem.nodes if abs(n.y) < tol or abs(n.y - WIDTH) < tol]
    elif which == "corner":
        found = [n for n in fem.nodes if abs(n.x) < tol and abs(n.y) < tol]
    elif which == "plate":
        found = list(fem.nodes)
    elif which == "half_x0":
        found = [n for n in fem.nodes if abs(n.x) < tol and n.y <= WIDTH / 2 + tol]
    elif which == "every_other_x0":
        along = sorted((n for n in fem.nodes if abs(n.x) < tol), key=lambda n: n.y)
        found = along[::2]
        if along[-1] not in found:
            found = found + [along[-1]]
    elif which == "interior":
        found = [n for n in fem.nodes if abs(n.x - LENGTH / 2) < tol and abs(n.y - WIDTH / 2) < tol]
    elif which == "mixed":
        found = [n for n in fem.nodes if abs(n.x) < tol and abs(n.y) < tol]
        found += [n for n in fem.nodes if abs(n.x - LENGTH / 2) < tol and abs(n.y - WIDTH / 2) < tol]
    else:  # pragma: no cover - the table above is closed
        raise AssertionError("no node group named {!r}".format(which))
    assert found, "no node matched {!r}".format(which)
    return sorted(found, key=lambda n: n.id)


def supported(assembly: ada.Assembly, *, which=SUPPORTS) -> ada.Assembly:
    """``assembly`` with a ``Bc`` per entry of ``which`` and one static step."""
    fem = assembly.get_by_name("Strip").fem
    for set_name, dofs, group in which:
        fem_set = fem.add_set(FemSet(set_name, nodes_where(fem, group), FemSet.TYPES.NSET, parent=fem))
        fem.add_bc(Bc(set_name, fem_set, list(dofs)))
    assembly.fem.add_step(StepImplicitStatic("static", nl_geom=False, total_time=1, init_incr=1, max_incr=1))
    return assembly


def one_bc(group: str, *, stiffened: bool = False, dofs=(1, 2, 3), seed: float = SEED) -> ada.Assembly:
    """The strip with a single ``Bc`` over the named node group."""
    return supported(strip(stiffened=stiffened, seed=seed), which=(("SUPPORT", dofs, group),))


def regions(assembly: ada.Assembly) -> dict:
    """``{set name: RegionPlan}`` for a model the writer accepts."""
    plan = build_plan(assembly, unit_scale=1.0, mesh_size=SEED, submit=False)
    return {region.cae_set_name: region for region in plan.analysis.regions}


# ------------------------------------------------------------------------- 1. a vertex region


def test_a_support_at_a_plate_corner_is_still_a_vertex_region():
    """Case 1, unchanged: a plate corner is a vertex of the emitted geometry, so it stays a
    ``Set(vertices=...)`` located by ``findAt``. The classifier tries this first because it is the
    narrowest of the three -- a corner node is also on two edges, and a vertex region is the object
    that says what the model meant."""
    region = regions(one_bc("corner"))["SUPPORT"]

    assert region.kind == "vertex"
    assert region.points == ((0.0, 0.0, 0.0),)
    assert region.boxes == ()
    assert region.node_count == 1


# --------------------------------------------------------------------------- 2. an edge region


def test_a_support_along_one_whole_plate_edge_becomes_an_edge_region():
    region = regions(one_bc("x0"))["SUPPORT"]

    assert region.kind == "edge"
    assert region.edge_count == 1
    assert region.total_length == pytest.approx(WIDTH)
    assert region.node_count == 5, "0.5 m at a 0.125 seed is four elements, five nodes"
    # Located by ONE box tight around the run, not by findAt at a point on it.
    assert region.points == ()
    assert len(region.boxes) == 1
    box = region.boxes[0]
    assert box.edge_count == 1
    assert box.length == pytest.approx(WIDTH)
    assert box.bounds == pytest.approx(
        (-EDGE_BOX_TOL, EDGE_BOX_TOL, -EDGE_BOX_TOL, WIDTH + EDGE_BOX_TOL, -EDGE_BOX_TOL, EDGE_BOX_TOL)
    )


def test_a_stiffener_splits_the_supported_edge_and_the_region_takes_both_sub_edges():
    """The case that makes ``findAt`` unusable. The bar's ends cut the ``x = 0`` boundary into two
    0.25 m sub-edges -- measured, the ACIS body carries 7 edges where the bare strip carries 4, and
    CAE imports exactly that -- so the region is a *chain*: one collinear run, two edges, still
    0.5 m. ``findAt`` at a point on it returns one edge of 0.25, which is half a simple support."""
    region = regions(one_bc("x0", stiffened=True))["SUPPORT"]

    assert region.kind == "edge"
    assert region.edge_count == 2
    assert region.total_length == pytest.approx(WIDTH)
    assert len(region.boxes) == 1, "the two sub-edges are collinear, so one box returns both"
    assert region.boxes[0].edge_count == 2
    assert region.boxes[0].bounds == pytest.approx(
        (-EDGE_BOX_TOL, EDGE_BOX_TOL, -EDGE_BOX_TOL, WIDTH + EDGE_BOX_TOL, -EDGE_BOX_TOL, EDGE_BOX_TOL)
    )


def test_a_support_on_two_parallel_edges_gets_one_box_each():
    """``CYL`` holds both long edges, and they are parallel rather than collinear. One box over the
    pair returned **7 edges totalling 13.0** -- the whole body -- against the 2 and 8.0 adapy
    predicts, so the region is one box per run."""
    region = regions(one_bc("long", dofs=(2, 4)))["SUPPORT"]

    assert region.kind == "edge"
    assert region.edge_count == 2
    assert region.total_length == pytest.approx(2 * LENGTH)
    assert len(region.boxes) == 2
    assert [box.edge_count for box in region.boxes] == [1, 1]
    assert [box.length for box in region.boxes] == pytest.approx([LENGTH, LENGTH])
    # Tight in y about each edge, which is what keeps the pair from swallowing the short ones.
    assert [(box.bounds[2], box.bounds[3]) for box in region.boxes] == pytest.approx(
        [(-EDGE_BOX_TOL, EDGE_BOX_TOL), (WIDTH - EDGE_BOX_TOL, WIDTH + EDGE_BOX_TOL)]
    )


def test_the_strips_three_supports_are_all_carried_and_are_the_same_at_every_seed():
    """The whole point of an edge region: it is geometry, so it does not move with the mesh.

    The boxes, the edge counts and the lengths are identical at three seeds a factor of two apart,
    while the node counts triple -- which is the property a region of located mesh nodes would not
    have had, and the reason the verification workstream's driver used geometry too."""
    shapes = {}
    for seed in (0.125, 0.0625, 0.03125):
        built = regions(supported(strip(seed=seed)))
        assert sorted(built) == ["CYL", "SS_X0", "SS_X1"]
        shapes[seed] = {
            name: (region.kind, region.edge_count, region.total_length, tuple(box.bounds for box in region.boxes))
            for name, region in built.items()
        }
        assert [built[name].node_count for name in ("CYL", "SS_X0", "SS_X1")] == {
            0.125: [66, 5, 5],
            0.0625: [130, 9, 9],
            0.03125: [258, 17, 17],
        }[seed]
    assert shapes[0.125] == shapes[0.0625] == shapes[0.03125]


# --------------------------------------------------------------------------- 3. a face region


def test_a_support_over_a_whole_plates_mesh_becomes_a_face_region():
    """Case 3. Resolved through ``Elem.refs`` -- adapy's own record of which plate a shell element
    was meshed from -- rather than geometrically, which is the same linkage a ``pressure`` uses. The
    region carries adapy's own area for the plate, which the emitted script asserts: measured, a
    ``DisplacementBC`` on such a ``Set(faces=...)`` restrained every one of the 165 nodes the face
    meshed into."""
    region = regions(one_bc("plate", dofs=(1, 2, 3, 4, 5, 6)))["SUPPORT"]

    assert region.kind == "face"
    assert region.plate_name == "strip"
    assert region.area == pytest.approx(LENGTH * WIDTH)
    assert len(region.points) == 1, "the bare strip is one face"
    assert region.node_count == 165
    assert region.boxes == ()


def test_a_face_region_takes_every_face_a_stiffener_split_the_plate_into():
    region = regions(one_bc("plate", stiffened=True, dofs=(1, 2, 3, 4, 5, 6)))["SUPPORT"]

    assert region.kind == "face"
    assert len(region.points) == 2, "the bar splits the strip into two faces, and both are in the set"
    assert region.area == pytest.approx(LENGTH * WIDTH)


# ------------------------------------------------------------------------------ 4. refusals


def refuse_half_an_edge() -> ada.Assembly:
    return one_bc("half_x0")


def refuse_every_other_node() -> ada.Assembly:
    return one_bc("every_other_x0")


def refuse_an_interior_node() -> ada.Assembly:
    return one_bc("interior")


def refuse_a_mixed_set() -> ada.Assembly:
    return one_bc("mixed")


def refuse_a_force_along_an_edge() -> ada.Assembly:
    assembly = strip()
    fem = assembly.get_by_name("Strip").fem
    fem_set = fem.add_set(FemSet("EDGE", nodes_where(fem, "x0"), FemSet.TYPES.NSET, parent=fem))
    step = assembly.fem.add_step(StepImplicitStatic("static", nl_geom=False, total_time=1, init_incr=1, max_incr=1))
    step.add_load(Load("PZ", Load.TYPES.FORCE, 1000.0, fem_set=fem_set, dof=[0, 0, 1, 0, 0, 0]))
    return assembly


def refuse_a_force_over_a_face() -> ada.Assembly:
    assembly = strip()
    fem = assembly.get_by_name("Strip").fem
    fem_set = fem.add_set(FemSet("WHOLE", nodes_where(fem, "plate"), FemSet.TYPES.NSET, parent=fem))
    step = assembly.fem.add_step(StepImplicitStatic("static", nl_geom=False, total_time=1, init_incr=1, max_incr=1))
    step.add_load(Load("PZ", Load.TYPES.FORCE, 1000.0, fem_set=fem_set, dof=[0, 0, 1, 0, 0, 0]))
    return assembly


#: Each a factory, built inside the test, with a phrase unique to *its own* refusal -- not a phrase
#: the generic "this is not a region" tail also carries. Every one of these is a model that would
#: otherwise open, mesh, solve and answer a different question from the one it was asked.
REFUSALS = {
    # The ends are not covered: the support would spread to the rest of the edge.
    # 0.25 to 0.5 rather than 0 to 0.25 because the ACIS body authored that boundary from
    # (0, 0.5, 0) towards (0, 0, 0): the parameter runs the other way, and the message says so.
    "a support on half a plate edge": (refuse_half_an_edge, r"reach only 0\.25 to 0\.5 of its 0\.5 length"),
    # The ends ARE covered and the middle is not, which a length check alone would have passed.
    "a support on every other node of an edge": (refuse_every_other_node, "the set holds 3 of the 5 node"),
    "a support at one interior node of a plate": (refuse_an_interior_node, "lies on no edge of the emitted geometry"),
    "a support on a corner and an interior node": (refuse_a_mixed_set, "1 of its 2 node"),
    "a force along a whole plate edge": (refuse_a_force_along_an_edge, "is not a line load"),
    "a force over a whole plate": (refuse_a_force_over_a_face, "A distributed load over a plate is a Pressure"),
}


@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_a_region_the_writer_cannot_carry_faithfully_is_refused_by_name(case, tmp_path):
    factory, expected = REFUSALS[case]
    assembly = factory()

    with pytest.raises(CaeWriteError, match=expected):
        assembly.to_abaqus_cae_script(tmp_path / "refused.py", mesh_size=SEED)


def geometry_of(assembly: ada.Assembly):
    """``(the vertex index, the edge index, the joint tolerance)`` for a model with no analysis.

    The two indices are what the writer resolves every record against, so a test that reaches
    :func:`classify_region` directly has to be given these rather than something of its own.
    """
    plan = build_plan(assembly, unit_scale=1.0, mesh_size=SEED, submit=False)
    return vertex_index(plan.parts, plan.joint_tol), edge_index(plan.parts, plan.joint_tol), plan.joint_tol


def test_the_refusal_names_the_nearest_vertex_and_the_nearest_edge():
    """A refusal nobody can act on is barely a refusal, so the message carries the measurement: the
    node, how far the nearest vertex is, and what the nearest edge is."""
    assembly = strip()
    fem = assembly.get_by_name("Strip").fem
    vertices, edges, tol = geometry_of(assembly)
    loose = fem.add_set(FemSet("MID", nodes_where(fem, "interior"), FemSet.TYPES.NSET, parent=fem))

    with pytest.raises(AnalysisNotSupported) as raised:
        classify_region(loose, vertices, edges, {}, tol, "the test's own support")

    message = str(raised.value)
    assert "(2.0, 0.25, 0.0)" in message, "the offending node"
    assert "the nearest vertex is at (" in message and "length units away" in message
    assert "the nearest edge is a plate boundary of part 'Strip'" in message


def test_a_set_that_is_a_whole_mesh_line_is_still_refused_on_a_longer_edge():
    """The coverage clause on its own, which the completeness clause does not subsume.

    On a mesh that conforms to the emitted geometry the two overlap: a set holding every node the FEM
    has on an edge reaches both its ends by construction, so either clause refuses a partial set. That
    premise is exactly what this writer does not assume anywhere else -- ``_vertex_region`` matches by
    *position* rather than by parentage for the same reason -- so the case is made here directly: an
    edge twice as long as the mesh line the set covers. Completeness passes (all five nodes the FEM has
    on that segment are in the set) and coverage refuses it, because carrying it would have supported
    1.0 m of structure where the model named 0.5.
    """
    from ada.cadit.cae.analysis import EdgeSegment

    assembly = strip()
    fem = assembly.get_by_name("Strip").fem
    vertices, _, tol = geometry_of(assembly)
    fem_set = fem.add_set(FemSet("SUPPORT", nodes_where(fem, "x0"), FemSet.TYPES.NSET, parent=fem))
    twice_as_long = [
        EdgeSegment(p1=(0.0, 0.0, 0.0), p2=(0.0, 2 * WIDTH, 0.0), cae_instance_name="Strip-1", owner="a longer edge")
    ]

    with pytest.raises(AnalysisNotSupported, match=r"reach only 0 to 0\.5 of its 1 length"):
        classify_region(fem_set, vertices, twice_as_long, {}, tol, "the test's own support")


def test_a_set_whose_nodes_belong_to_no_fem_cannot_be_shown_to_cover_an_edge():
    """A detached ``FemSet`` is refused rather than accepted on the length check alone.

    "Every node the model has on this edge" is the clause that makes an edge region safe, and it can
    only be asked of a set that knows which FEM it belongs to. A set with no parent therefore falls
    through to the refusal instead of being carried on the two clauses that *can* be checked.

    Reached through :func:`classify_region` rather than through the writer, because adapy's own
    ``FEM.add_bc`` adopts a parentless set into ``fem.sets`` -- so this state is not reachable from
    the public API and the branch exists to be safe rather than to be met.
    """
    assembly = strip()
    fem = assembly.get_by_name("Strip").fem
    vertices, edges, tol = geometry_of(assembly)
    detached = FemSet("LOOSE", nodes_where(fem, "x0"), FemSet.TYPES.NSET)

    with pytest.raises(AnalysisNotSupported, match="not attached to a FEM"):
        classify_region(detached, vertices, edges, {}, tol, "the test's own support")


# ------------------------------------------------------------------------------ the emission


def test_the_emitted_script_locates_each_edge_region_by_box_and_checks_its_length(tmp_path):
    written = supported(strip(stiffened=True)).to_abaqus_cae_script(
        tmp_path / "strip.py", mesh_size=SEED, shell_element_type="S4R"
    )
    source = written[0].read_text(encoding="utf-8")

    assert "findAt" in source, "the plate faces are still located by findAt"
    graph = check_emitted_script(source, name="strip.py")
    calls = graph.by_method("_analysis_edge_region")
    assert len(calls) == 3
    by_name = {call.args[1]: call for call in calls}
    assert sorted(by_name) == ["CYL", "SS_X0", "SS_X1"]
    # SS_X0 is the stiffener-split boundary: one box, two edges, half a metre.
    boxes, edge_count, total_length = by_name["SS_X0"].args[3:6]
    assert len(boxes) == 1
    assert boxes[0][1] == 2
    assert edge_count == 2
    assert total_length == pytest.approx(WIDTH)
    # CYL is the parallel pair: two boxes of one edge each.
    boxes, edge_count, total_length = by_name["CYL"].args[3:6]
    assert [box[1] for box in boxes] == [1, 1]
    assert edge_count == 2
    assert total_length == pytest.approx(2 * LENGTH)
    # The DOFs, and the region each support names.
    for bc in graph.by_method("DisplacementBC"):
        assert bc.raw_kwargs["region"].endswith("['{}']".format(bc.kw("name")))
    assert "REGION_LENGTH_REL_TOL" in source
    assert "getByBoundingBox" in source
    assert graph.by_method("_analysis_region") == [], "no support of this model is at a vertex"


def test_a_face_region_is_emitted_with_the_plates_own_area(tmp_path):
    written = one_bc("plate", dofs=(1, 2, 3, 4, 5, 6)).to_abaqus_cae_script(
        tmp_path / "fixed.py", mesh_size=SEED, shell_element_type="S4R"
    )
    source = written[0].read_text(encoding="utf-8")

    graph = check_emitted_script(source, name="fixed.py")
    calls = graph.by_method("_analysis_face_region")
    assert len(calls) == 1
    name, instance, points, area, plate_name = calls[0].args[1:6]
    assert (name, instance, plate_name) == ("SUPPORT", "Strip-1", "strip")
    assert len(points) == 1
    assert area == pytest.approx(LENGTH * WIDTH)
    assert "REGION_AREA_REL_TOL" in source


def test_a_beams_only_model_emits_no_edge_region_call_and_no_length_tolerance(tmp_path):
    """What the script *does* is a statement about this model. A frame supported at its bases makes
    no bounding-box call and carries no region tolerance -- the two tolerance constants are emitted
    per region kind, so their absence says the classification came out vertices.

    The helper *definitions* are in the script either way, as ``_pressure_surface`` already is for a
    model with no pressure: they live in one block and what says which of them this model needed is
    the calls."""
    mat = ada.Material("S355", CarbonSteel("S355"))
    part = ada.Part("Frame")
    part / (ada.Beam("col", (0, 0, 0), (0, 0, 4.0), "IPE300", mat),)
    part.fem = part.to_fem_obj(1.0, "line")
    base = part.fem.add_set(FemSet("BASE", nodes_where_beam(part.fem, (0.0, 0.0, 0.0)), FemSet.TYPES.NSET))
    part.fem.add_bc(Bc("FIX", base, [1, 2, 3, 4, 5, 6]))
    assembly = ada.Assembly("Site") / part
    assembly.fem.add_step(StepImplicitStatic("static", nl_geom=False, total_time=1, init_incr=1, max_incr=1))

    written = assembly.to_abaqus_cae_script(tmp_path / "frame.py", mesh_size=1.0)
    source = written[0].read_text(encoding="utf-8")

    graph = check_emitted_script(source, name="frame.py")
    assert len(graph.by_method("_analysis_region")) == 1
    assert graph.by_method("_analysis_edge_region") == []
    assert graph.by_method("_analysis_face_region") == []
    assert "REGION_LENGTH_REL_TOL = " not in source
    assert "REGION_AREA_REL_TOL = " not in source


def nodes_where_beam(fem, point) -> list:
    found = fem.nodes.get_by_volume(p=point, tol=1e-06)
    assert len(found) == 1, "expected one node at {}".format(point)
    return list(found)
