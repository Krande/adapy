"""BEUSLO: the record a pressure becomes, and everything BEUSLO cannot carry.

The layout is verified against the solver in ``tests/fem/test_sesam_pressure_load.py``; these
are the field-by-field assertions that hold the writer to it without a licence, plus the
refusals, which Sestra cannot check because a refused load never reaches it.
"""

import ada
from ada.fem import Elem, FemSet, Load, Surface
from ada.fem.containers import FemElements
from ada.fem.formats import conversion_report
from ada.fem.formats.sesam.write.write_loads import (
    PRESSURE_INTNO,
    PRESSURE_LAYER,
    PRESSURE_LOTYP,
    SIDE_NEGATIVE,
    SIDE_POSITIVE,
    load_pressure,
    loads_str,
)
from ada.fem.loads import LoadPressure
from ada.fem.shapes.definitions import LineShapes, ShellShapes, SolidShapes
from ada.fem.steps import StepImplicitStatic

Q = 1000.0


def _records(out: str) -> list[list[list[float]]]:
    """Each BEUSLO record as its three rows of numbers, in the order written."""
    lines = out.splitlines()
    recs = []
    for i, line in enumerate(lines):
        if not line.startswith("BEUSLO"):
            continue
        rows = [[float(x) for x in line.split()[1:]]]
        rows += [[float(x) for x in lines[i + 1].split()], [float(x) for x in lines[i + 2].split()]]
        recs.append(rows)
    return recs


def _quad_fem(n_elements: int = 2, shape=ShellShapes.QUAD) -> tuple[ada.FEM, list[Elem]]:
    """A strip of ``n_elements`` shells sharing edges, with node ids counting from 1."""
    per_row = {ShellShapes.QUAD: 4, ShellShapes.TRI: 3, ShellShapes.QUAD8: 8}[shape]
    nodes = []
    elements = []
    nid = 1
    for i in range(n_elements):
        el_nodes = [ada.Node((i + k * 0.1, k * 0.2, 0.0), nid + k) for k in range(per_row)]
        nodes += el_nodes
        nid += per_row
        elements.append(Elem(i + 1, el_nodes, shape))
    fem = ada.FEM("MyFem", nodes=ada.api.containers.Nodes(nodes))
    fem.elements = FemElements(elements, fem_obj=fem)
    for el in elements:
        el.parent = fem
    return fem, elements


def _elset_pressure(fem, elements, magnitude=Q, name="q"):
    es = fem.add_set(FemSet("SHELLS", elements, FemSet.TYPES.ELSET, parent=fem))
    return Load(name, Load.TYPES.PRESSURE, magnitude, fem_set=es)


def test_beuslo_field_layout_for_a_quad_pressure():
    """``LLC LOTYP COMPLX LAYER / ELNO NDOF INTNO SIDE / RLOAD1..RLOADn``.

    Written out as literals rather than against the module's own constants: asserting
    ``head[1] == PRESSURE_LOTYP`` is true whatever ``PRESSURE_LOTYP`` is, and a mutation run
    caught all three of LOTYP, LAYER and SIDE passing that way.
    """
    fem, elements = _quad_fem(2)
    out = load_pressure(_elset_pressure(fem, elements), 7)

    recs = _records(out)
    assert len(recs) == 2, "one record per element"
    for el, (head, ident, intensities) in zip(elements, recs):
        # LLC = the load case, LOTYP 1 = a surface pressure, COMPLX 0 = no phase, LAYER 0 =
        # unlayered (Sestra: "Layered elements is not supported in this version").
        assert head == [7.0, 1.0, 0.0, 0.0]
        # ELNO (internal = external here), NDOF = the node count, INTNO 0 = the default
        # integration rule, SIDE 1 = the face on the element's positive normal.
        assert ident == [float(el.id), 4.0, 0.0, 1.0]
        # The pressure itself, once per node -- not a force, and not a per-node share of one.
        assert intensities == [Q, Q, Q, Q]


def test_the_record_constants_are_the_values_sestra_accepts():
    """The four constants, each against what Sestra V11.3-00 was measured to do with it.

    A companion to the literals above: this is where the constants are pinned, so a change to
    one is a change to a line that says why it is what it is.
    """
    assert PRESSURE_LOTYP == 1, "LOTYP 2 wants three components per node; LOTYP 3 is dropped silently"
    assert PRESSURE_LAYER == 0, "LAYER != 0 is a layered element, which Sestra warns about and ignores"
    assert PRESSURE_INTNO == 0, "INTNO != 0 is a non-default integration rule, which Sestra warns about and ignores"
    assert (SIDE_POSITIVE, SIDE_NEGATIVE) == (1, 2), "SIDE 0 and 4+ are 'Illegal side index' on an FQUS"


def test_ndof_is_the_element_node_count():
    """Sestra refuses a vector shorter than the element's node count: "Load intensity vector
    size does not match dof count for load"."""
    for shape, n in ((ShellShapes.TRI, 3), (ShellShapes.QUAD, 4), (ShellShapes.QUAD8, 8)):
        fem, elements = _quad_fem(1, shape=shape)
        head, ident, intensities = _records(load_pressure(_elset_pressure(fem, elements), 1))[0]
        assert ident[1] == float(n), shape
        assert intensities == [Q] * n, shape


def test_a_negative_face_surface_flips_the_sign_and_names_side_2():
    """Abaqus puts a positive ``*Dsload P`` into the face it names; BEUSLO always pushes along
    the element's negative normal, so only the sign can carry a SNEG pressure."""
    fem, elements = _quad_fem(2)
    es = fem.add_set(FemSet("SHELLS", elements, FemSet.TYPES.ELSET, parent=fem))
    surf = Surface("SNEG_SURF", Surface.TYPES.ELEMENT, es, el_face_index=-1, parent=fem)

    recs = _records(load_pressure(LoadPressure("q", Q, surf), 1))
    assert len(recs) == 2
    for _head, ident, intensities in recs:
        assert ident[3] == 2.0, "SIDE 2 records that the model meant the negative face"
        assert intensities == [-Q, -Q, -Q, -Q]

    # ... and the positive face keeps the magnitude as given.
    surf_pos = Surface("SPOS_SURF", Surface.TYPES.ELEMENT, es, el_face_index=1, parent=fem)
    for _head, ident, intensities in _records(load_pressure(LoadPressure("q", Q, surf_pos), 1)):
        assert ident[3] == 1.0
        assert intensities == [Q, Q, Q, Q]


def test_a_plain_element_set_is_the_positive_face():
    """A set names no side, and Abaqus' own default for a shell surface is SPOS."""
    fem, elements = _quad_fem(1)
    _head, ident, intensities = _records(load_pressure(_elset_pressure(fem, elements), 1))[0]
    assert ident[3] == 1.0
    assert intensities[0] == Q


def test_records_are_written_in_element_id_order():
    """Determinism: the set is a list and a surface may name several."""
    fem, elements = _quad_fem(4)
    shuffled = [elements[2], elements[0], elements[3], elements[1]]
    out = load_pressure(_elset_pressure(fem, shuffled), 1)
    assert [rec[1][0] for rec in _records(out)] == [1.0, 2.0, 3.0, 4.0]


def test_a_pressure_on_beams_is_refused_by_name():
    nodes = [ada.Node((i, 0, 0), i + 1) for i in range(3)]
    fem = ada.FEM("MyFem", nodes=ada.api.containers.Nodes(nodes))
    beams = [Elem(1, nodes[:2], LineShapes.LINE), Elem(2, nodes[1:], LineShapes.LINE)]
    fem.elements = FemElements(beams, fem_obj=fem)
    for el in beams:
        el.parent = fem

    with conversion_report.collect() as rep:
        out = load_pressure(_elset_pressure(fem, beams), 1)
    assert out == "", "no BEUSLO for a beam"
    findings = [f for f in rep.findings if f.subject == "q"]
    assert len(findings) == 1, findings
    assert "BEUSLO is a shell surface load" in findings[0].reason
    assert findings[0].details["n_elements"] == 2


def test_a_pressure_on_solids_is_refused_by_name():
    """BEUSLO's SIDE numbering for a solid face is not established here, so it is refused
    rather than written with a guessed face."""
    nodes = [ada.Node((i % 2, (i // 2) % 2, i // 4), i + 1) for i in range(8)]
    fem = ada.FEM("MyFem", nodes=ada.api.containers.Nodes(nodes))
    hexes = [Elem(1, nodes, SolidShapes.HEX8)]
    fem.elements = FemElements(hexes, fem_obj=fem)
    hexes[0].parent = fem

    with conversion_report.collect() as rep:
        out = load_pressure(_elset_pressure(fem, hexes), 1)
    assert out == ""
    assert any("shell surface load" in f.reason for f in rep.findings if f.subject == "q")


def test_a_pressure_on_a_node_set_is_refused_by_name():
    fem, elements = _quad_fem(1)
    nset = fem.add_set(FemSet("NODES", list(fem.nodes), FemSet.TYPES.NSET, parent=fem))
    with conversion_report.collect() as rep:
        out = load_pressure(Load("q", Load.TYPES.PRESSURE, Q, fem_set=nset), 1)
    assert out == ""
    assert any("node-based surface" in f.reason for f in rep.findings if f.subject == "q")


def test_a_pressure_on_a_node_based_surface_is_refused_by_name():
    fem, elements = _quad_fem(1)
    nset = fem.add_set(FemSet("NODES", list(fem.nodes), FemSet.TYPES.NSET, parent=fem))
    surf = Surface("NODE_SURF", Surface.TYPES.NODE, nset, parent=fem)
    with conversion_report.collect() as rep:
        out = load_pressure(LoadPressure("q", Q, surf), 1)
    assert out == ""
    assert any("node-based surface" in f.reason for f in rep.findings if f.subject == "q")


def test_a_shell_shape_with_no_sesam_element_type_is_refused_by_name():
    """TRI7 has no Sesam element type, so it is not in the deck and nothing can load it."""
    nodes = [ada.Node((i, 0, 0), i + 1) for i in range(7)]
    fem = ada.FEM("MyFem", nodes=ada.api.containers.Nodes(nodes))
    els = [Elem(1, nodes, ShellShapes.TRI7)]
    fem.elements = FemElements(els, fem_obj=fem)
    els[0].parent = fem
    with conversion_report.collect() as rep:
        out = load_pressure(_elset_pressure(fem, els), 1)
    assert out == ""
    assert any("no Sesam element type" in f.reason for f in rep.findings if f.subject == "q")


def test_a_total_force_pressure_is_refused_by_name():
    """``LoadPressure`` with ``TOTAL_FORCE`` carries a force, not an intensity; the Abaqus
    writer raises on the same thing."""
    fem, elements = _quad_fem(1)
    es = fem.add_set(FemSet("SHELLS", elements, FemSet.TYPES.ELSET, parent=fem))
    surf = Surface("S", Surface.TYPES.ELEMENT, es, el_face_index=1, parent=fem)
    load = LoadPressure("q", Q, surf, distribution=LoadPressure.P_DIST_TYPES.TOTAL_FORCE)
    with conversion_report.collect() as rep:
        out = load_pressure(load, 1)
    assert out == ""
    assert any("pressure intensity" in f.reason for f in rep.findings if f.subject == "q")


def test_a_mixed_set_writes_the_shells_and_refuses_the_rest_by_name():
    """A set holding shells and beams must not cost the shells their load, nor pass the beams
    over in silence."""
    fem, shells = _quad_fem(2)
    extra = [ada.Node((9, 0, 0), 100), ada.Node((10, 0, 0), 101)]
    for n in extra:
        fem.nodes.add(n)
    beam = Elem(50, extra, LineShapes.LINE)
    beam.parent = fem
    fem.elements = FemElements(shells + [beam], fem_obj=fem)

    with conversion_report.collect() as rep:
        out = load_pressure(_elset_pressure(fem, shells + [beam]), 1)
    assert [rec[1][0] for rec in _records(out)] == [1.0, 2.0]
    assert any("shell surface load" in f.reason for f in rep.findings if f.subject == "q")


def test_the_step_writes_tdload_then_the_beuslo_block():
    """A pressure is a load case like any other: it no longer leaves the deck unloaded."""
    fem, elements = _quad_fem(2)
    step = StepImplicitStatic("static", total_time=1, init_incr=1, max_incr=1)
    step.add_load(_elset_pressure(fem, elements))
    fem.add_step(step)

    out = loads_str(fem)
    assert out.splitlines()[0].startswith("TDLOAD")
    assert out.count("BEUSLO") == 2
    # LLC on every record is the case TDLOAD just declared.
    assert {rec[0][0] for rec in _records(out)} == {1.0}


def test_the_pressure_is_no_longer_reported_omitted():
    fem, elements = _quad_fem(1)
    step = StepImplicitStatic("static", total_time=1, init_incr=1, max_incr=1)
    step.add_load(_elset_pressure(fem, elements))
    fem.add_step(step)

    with conversion_report.collect() as rep:
        out = loads_str(fem)
    assert "BEUSLO" in out
    assert not [f for f in rep.findings if "pressure" in str(f.reason) and f.subject == "q"], [
        f.reason for f in rep.findings
    ]


def test_an_unsupported_load_type_is_still_reported_omitted():
    """The omission path this change narrowed must still fire for what it was written for."""
    fem, elements = _quad_fem(1)
    step = StepImplicitStatic("static", total_time=1, init_incr=1, max_incr=1)
    step.add_load(Load("m", "mass", 1.0))
    fem.add_step(step)
    with conversion_report.collect() as rep:
        assert loads_str(fem).count("BEUSLO") == 0
    assert any('a "mass" load is not written' in f.reason for f in rep.findings)


def test_the_surface_gap_table_names_the_pressure_it_now_carries():
    """A ``Surface`` still has no card of its own, but the report must not read as though one
    carrying a pressure had been lost."""
    from ada.fem.formats.sesam.write.not_held import report_tables

    fem, elements = _quad_fem(1)
    es = fem.add_set(FemSet("SHELLS", elements, FemSet.TYPES.ELSET, parent=fem))
    fem.add_surface(Surface("S", Surface.TYPES.ELEMENT, es, el_face_index=1, parent=fem))

    with conversion_report.collect() as rep:
        report_tables([fem])
    reasons = [f.reason for f in rep.findings if f.keyword == "Surface"]
    assert len(reasons) == 1, reasons
    assert "BEUSLO" in reasons[0], reasons[0]
