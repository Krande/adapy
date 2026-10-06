"""GeniE supports read into ``Part.concept_fem.constraints``, checked against GeniE's own mesh.

The fixtures are GeniE V8.13-02 exports of two probe models, unchanged but for the user name:

* ``genie_supports_all_kinds.xml`` -- every support kind GeniE offers on beams Bm1..Bm7 and plates
  Pl1..Pl3: fixed, pinned, roller and mixed points, a point inside a beam's span, springs, a
  prescribed DOF with a ``PrescribedDisplacement`` of -0.01 in dz, dependent and super DOFs, a
  45-degree point, support curves along a whole beam, part of a beam and plate edges, a curve
  spring of 500000 N/m^2, a curve with a 45-degree ``setLocalX``, and a rigid link.
* ``genie_supports_frames.xml`` -- the frames: guide-oriented curves along +y, -x, +z, -z and a
  3-4-5 slope, constant-frame curves (identity, and x' = y), points turned 90 degrees (one a
  spring, one prescribed), a prescribed point with displacements in two load cases, and rigid
  links rotation-dependent, with a free slave dx, including only supports' nodes, and turned.

The expected values are what GeniE's ``T1.FEM`` for each model carries, turned into global axes
through its BNTRCOS where it gave one (e.g. Sc_y_dx: BNBCD 1 0 0 0 1 0 under x' = y holds dy and
rx). ``tests/fem/test_genie_gxml_supports.py`` checks the same against GeniE when it is installed.
"""

from __future__ import annotations

import pathlib
import shutil
import xml.etree.ElementTree as ET

import pytest

import ada
from ada.fem.concept.loads import LoadConceptPrescribedDisplacement
from ada.fem.formats import conversion_report

STAGE = "genie xml reader"
USER_MODEL = pathlib.Path(r"C:\AibelProgs\projects\temp\genie_cae\temp\Assembly\Assembly.gnx")


def _read(path: pathlib.Path):
    with conversion_report.collect() as report:
        a = ada.from_genie_xml(path)
    (part,) = a.get_all_subparts()
    return a, part, report


def _copy(example_files, tmp_path, fixture) -> pathlib.Path:
    # Read a copy: the reader writes a .sat beside the XML it is given.
    dst = tmp_path / fixture
    shutil.copy(example_files / "fem_files" / "sesam" / fixture, dst)
    return dst


def _dofs(concept) -> dict:
    """``{dof: constraint}``, with a spring as ``("spring", k)``; free dofs left out."""
    out = {}
    for d in concept.dof_constraints:
        if d.constraint_type == "spring":
            out[d.dof] = ("spring", d.spring_stiffness)
        elif d.constraint_type != "free":
            out[d.dof] = d.constraint_type
    return out


def _findings(report) -> dict[str, list]:
    """Per subject, the (keyword, count) of every finding of this reader."""
    out = {}
    for f in report.findings:
        if f.stage == STAGE:
            for subject in [f.subject, *f.other_subjects]:
                out.setdefault(subject, []).append((f.keyword, f.count))
    return out


def _p(point) -> tuple:
    return tuple(float(c) for c in point)


@pytest.fixture
def all_kinds(example_files, tmp_path):
    return _read(_copy(example_files, tmp_path, "genie_supports_all_kinds.xml"))


@pytest.fixture
def frames(example_files, tmp_path):
    return _read(_copy(example_files, tmp_path, "genie_supports_frames.xml"))


# --- genie_supports_all_kinds.xml --------------------------------------------------------------

#: BNBCD codes at each point support's node in GeniE's deck: 1 fixed, 3 dependent, 4 super.
ALL_KINDS_POINTS = {
    "Sp_fixed": ((0, 0, 0), dict.fromkeys(("dx", "dy", "dz", "rx", "ry", "rz"), "fixed")),
    "Sp_pinned": ((4, 0, 0), dict.fromkeys(("dx", "dy", "dz"), "fixed")),
    "Sp_roller": ((0, 1.5, 0), {"dz": "fixed"}),
    "Sp_mixed": ((4, 1.5, 0), {"dx": "fixed", "dz": "fixed", "rx": "fixed"}),
    "Sp_mid": ((1.3, 1.5, 0), {"dy": "fixed"}),
    "Sp_dep": ((4, 16, 0), {"dx": "dependent", "dy": "fixed", "dz": "fixed"}),
    "Sp_super": ((0, 16, 0), dict.fromkeys(("dx", "dy", "dz", "rx", "ry", "rz"), "super")),
    "Sp_presc": ((4, 8, 0), {"dx": "fixed", "dy": "fixed", "dz": "prescribed"}),
    # MGSPRNG diagonal 1e6 at K33 and 2e5 at K55; BNBCD 0 at those dofs
    "Sp_spring": (
        (0, 6, 0),
        {"dx": "fixed", "dy": "fixed", "dz": ("spring", 1e6), "rx": "fixed", "ry": ("spring", 2e5), "rz": "fixed"},
    ),
    "Sp_spring2": ((0, 6, 4), {"dy": ("spring", 2e6)}),
}

#: The nodes GeniE put each curve's codes on: the curve's ends, and its dofs.
ALL_KINDS_CURVES = {
    "Sc_beam": ((0, 12, 0), (4, 12, 0), dict.fromkeys(("dx", "dy", "dz"), "fixed")),
    "Sc_partial": ((1, 14, 0), (3, 14, 0), {"dz": "fixed"}),  # BNBCD on the 5 nodes x = 1..3
    # along y, in the guide frame x' = y (BNTRCOS); all six fixed is the same in any frame
    "Sc_edge": ((0, 3, 0), (0, 4, 0), dict.fromkeys(("dx", "dy", "dz", "rx", "ry", "rz"), "fixed")),
    "Sc_edge_dz": ((4, 3, 0), (4, 4, 0), {"dz": "fixed"}),
    # 500000 N/m^2 per length: MGSPRNG 125000 at the end nodes, 250000 inside, at 0.5 m
    "Sc_spring": ((0, 10, 0), (4, 10, 0), {"dx": "fixed", "dy": "fixed", "dz": ("spring", 5e5)}),
}


def test_every_point_support_is_read_with_its_dofs(all_kinds):
    _, part, _ = all_kinds
    points = part.concept_fem.constraints.point_constraints
    assert sorted(points) == sorted(ALL_KINDS_POINTS)
    for name, (position, dofs) in ALL_KINDS_POINTS.items():
        assert _p(points[name].position) == pytest.approx(position), name
        assert _dofs(points[name]) == dofs, name


def test_a_spring_is_read_with_its_stiffness(all_kinds):
    """It was read as constraint "spring" with stiffness 0.0, the attribute dropped."""
    _, part, _ = all_kinds
    sp = {d.dof: d for d in part.concept_fem.constraints.point_constraints["Sp_spring"].dof_constraints}
    assert (sp["dz"].constraint_type, sp["dz"].spring_stiffness) == ("spring", 1_000_000.0)
    assert (sp["ry"].constraint_type, sp["ry"].spring_stiffness) == ("spring", 200_000.0)
    sc = {d.dof: d for d in part.concept_fem.constraints.curve_constraints["Sc_spring"].dof_constraints}
    assert sc["dz"].spring_stiffness == 500_000.0


def test_every_support_curve_is_read_with_its_ends_and_dofs(all_kinds):
    _, part, _ = all_kinds
    curves = part.concept_fem.constraints.curve_constraints
    assert sorted(curves) == sorted(ALL_KINDS_CURVES)
    for name, (start, end, dofs) in ALL_KINDS_CURVES.items():
        assert (_p(curves[name].start_pos), _p(curves[name].end_pos)) == (start, end), name
        assert _dofs(curves[name]) == dofs, name


def test_the_rigid_link_is_read_with_its_box_and_translation_only_link(all_kinds):
    """GeniE linked the 6 nodes of Pl3 inside (0..2, 19..20, -0.2..0) with 9-term BLDEPs (slave
    translations only), and fixed all six dofs of the master at (2, 20, 1)."""
    _, part, _ = all_kinds
    (rl,) = part.concept_fem.constraints.rigid_links.values()
    assert rl.name == "Srl"
    assert _p(rl.master_point) == (2, 20, 1)
    assert _p(rl.influence_region.lower_corner) == (0, 19, -0.2)
    assert _p(rl.influence_region.upper_corner) == (2, 20, 0)
    assert _dofs(rl) == dict.fromkeys(("dx", "dy", "dz", "rx", "ry", "rz"), "fixed")
    assert (rl.rotation_dependent, rl.include_all_edges) == (False, True)


def test_the_prescribed_displacement_is_read_with_its_value(all_kinds):
    """GeniE: BNBCD 2 at dz of Sp_presc's node, BNDISPL dz -0.00999999978 in load case 2."""
    _, part, _ = all_kinds
    lc = part.concept_fem.loads.load_cases["LC_presc"]
    (pd,) = lc.loads
    assert isinstance(pd, LoadConceptPrescribedDisplacement)
    assert pd.name == "PD_presc"
    assert pd.support is part.concept_fem.constraints.point_constraints["Sp_presc"]
    assert (pd.translation, pd.rotation) == ((0.0, 0.0, -0.01), (0.0, 0.0, 0.0))
    assert pd.parent is lc


def test_the_two_rotated_supports_are_refused_once_each(all_kinds):
    _, part, report = all_kinds
    assert _findings(report) == {"Sp_local": [("local_system", 1)], "Sc_local": [("local_system", 1)]}
    constraints = part.concept_fem.constraints
    assert "Sp_local" not in constraints.point_constraints
    assert "Sc_local" not in constraints.curve_constraints


# --- genie_supports_frames.xml -----------------------------------------------------------------

#: Each support whose frame is not the identity, with the global dofs GeniE's codes hold through
#: the node's BNTRCOS.
FRAMES = {
    # guide frames
    "Sc_y_dx": ({"dy": "fixed", "rx": "fixed"}, (0, 0, 0), (0, 1, 0)),  # x' = y: dx', ry' -> dy, rx
    "Sc_negx": ({"dy": "fixed", "rx": "fixed"}, (4, 0, 0), (0, 0, 0)),  # x' = -x: dy', rx' -> dy, rx
    "Sc_z": ({"dz": "fixed"}, (0, 6, 0), (0, 6, 2)),  # x' = z
    "Sc_negz": ({"dz": "fixed"}, (8, 3, 2), (8, 3, 0)),  # x' = -z
    # constant frames
    "Sc_y_const": ({"dx": "fixed", "ry": "fixed"}, (4, 0, 0), (4, 1, 0)),  # identity: no BNDOF
    "Sc_x_rot": ({"dy": "fixed", "rz": "fixed"}, (0, 1, 0), (4, 1, 0)),  # x' = y
    # points and a rigid link's master, x' = y
    "Sp_rot90": ({"dy": "fixed", "dz": "fixed"}, (4, 6, 0)),
    "Sp_spring_rot": ({"dy": ("spring", 1e6), "dz": "fixed"}, (4, 8, 0)),  # MGSPRNG K11 = 1e6 under x' = y
    "Srl_frame": ({"dy": "fixed", "dz": "fixed"}, (2, 22, 1)),
}


@pytest.mark.parametrize("name", sorted(FRAMES))
def test_a_rotated_support_is_read_in_global_axes(frames, name):
    _, part, _ = frames
    c = part.concept_fem.constraints
    concept = {**c.point_constraints, **c.curve_constraints, **c.rigid_links}[name]
    dofs, *where = FRAMES[name]
    assert _dofs(concept) == dofs
    if len(where) == 2:
        assert (_p(concept.start_pos), _p(concept.end_pos)) == tuple(where)
    else:
        position = concept.position if hasattr(concept, "position") else concept.master_point
        assert _p(position) == where[0]


def test_rigid_link_options_are_read(frames):
    """Srl_rot: slave_r*="dependent", 12-term BLDEP and BNBCD 3 on all six slave dofs. Srl_only:
    IncludeOnlyNodesOfSupports, written include_all_edges="false"."""
    _, part, _ = frames
    links = part.concept_fem.constraints.rigid_links
    assert sorted(links) == ["Srl_frame", "Srl_only", "Srl_rot"]
    assert (links["Srl_rot"].rotation_dependent, links["Srl_rot"].include_all_edges) == (True, True)
    assert (links["Srl_only"].rotation_dependent, links["Srl_only"].include_all_edges) == (False, False)
    assert (links["Srl_frame"].rotation_dependent, links["Srl_frame"].include_all_edges) == (False, True)


def test_one_support_prescribed_in_two_load_cases(frames):
    """GeniE: BNBCD 2 1 2 0 0 2 at (14, 0, 0); BNDISPL -0.003 dx in LC1, and 0.005 dx, -0.01 dz,
    0.001 rz in LC2."""
    _, part, _ = frames
    sp = part.concept_fem.constraints.point_constraints["Sp_presc"]
    assert _dofs(sp) == {"dx": "prescribed", "dy": "fixed", "dz": "prescribed", "rz": "prescribed"}
    loads = {
        lc.name: [ld for ld in lc.loads if isinstance(ld, LoadConceptPrescribedDisplacement)]
        for lc in part.concept_fem.loads.load_cases.values()
    }
    (pd_b,) = loads["LC1"]
    (pd_a,) = loads["LC2"]
    assert (pd_b.name, pd_b.support, pd_b.translation, pd_b.rotation) == ("PD_b", sp, (-0.003, 0, 0), (0, 0, 0))
    assert (pd_a.name, pd_a.support, pd_a.translation, pd_a.rotation) == ("PD_a", sp, (0.005, 0, -0.01), (0, 0, 0.001))


def test_the_frames_fixture_refuses_exactly_four_constructs(frames):
    _, part, report = frames
    assert _findings(report) == {
        # dx' alone along a 3-4-5 slope holds no global axis
        "Sc_slope": [("local_system", 1)],
        # its value in LC2 is along x' = y
        "Sp_presc_rot": [("local_system", 1)],
        # slave_dx="free": BLDEP left dx out
        "Srl_sdx": [("support_rigid_link", 1)],
        # and the displacement of the refused support with it
        "PD_rot in load case LC2": [("footprint_support_point", 1)],
    }
    c = part.concept_fem.constraints
    assert "Sc_slope" not in c.curve_constraints and "Sp_presc_rot" not in c.point_constraints
    assert "Srl_sdx" not in c.rigid_links
    assert [ld.name for ld in part.concept_fem.loads.load_cases["LC2"].loads] == ["PD_a"]


# --- refusals of constructs the fixtures do not hold -------------------------------------------


def _edit(example_files, tmp_path, fixture, edit) -> pathlib.Path:
    path = _copy(example_files, tmp_path, fixture)
    tree = ET.parse(path)
    edit(tree.getroot())
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return path


def _support(root, name):
    (el,) = [e for e in root.iter() if e.tag.startswith("support_") and e.get("name") == name]
    return el


def _mixed_slave_rotations(root):
    _support(root, "Srl_rot").attrib.pop("slave_rz")


def _rotated_footprint(root):
    box = _support(root, "Srl_rot").find("region/footprint_box/local_system")
    for v in box:
        x, y = v.get("x"), v.get("y")
        v.set("x", str(-float(y)))
        v.set("y", x)


def _disagreeing_rotation_dependent(root):
    _support(root, "Srl_rot").set("rotation_dependent", "false")


def _five_dofs(root):
    bcs = _support(root, "Sp_rot90").find("boundary_conditions")
    bcs.remove(bcs[-1])


def _spring_without_stiffness(root):
    bc = _support(root, "Sp_spring_rot").find("boundary_conditions/boundary_condition[@constraint='spring']")
    bc.attrib.pop("stiffness")


def _unknown_child(root):
    ET.SubElement(_support(root, "Sp_rot90"), "description")


def _curve_of_three_points(root):
    guide = _support(root, "Sc_z").find("geometry/wire/guide")
    ET.SubElement(guide, "position", {"x": "0", "y": "6", "z": "1", "end": "3"})


def _other_line_orientation(root):
    orientation = _support(root, "Sc_x_rot").find("line_orientation")
    orientation[0].tag = "guide_line_orientation"


def _support_elsewhere(root):
    el = _support(root, "Sp_rot90")
    for structure in root.iter("structure"):
        if el in list(structure):
            structure.remove(el)
    root.find("./model").append(el)


def _unknown_support_kind(root):
    _support(root, "Sp_rot90").tag = "support_surface"


REFUSALS = {
    "mixed slave rotations": (_mixed_slave_rotations, "Srl_rot", "support_rigid_link"),
    "rotated footprint box": (_rotated_footprint, "Srl_rot", "footprint_box"),
    "rotation_dependent against slave_r*": (_disagreeing_rotation_dependent, "Srl_rot", "support_rigid_link"),
    "five dofs": (_five_dofs, "Sp_rot90", "boundary_conditions"),
    "spring without stiffness": (_spring_without_stiffness, "Sp_spring_rot", "boundary_condition"),
    "unknown child": (_unknown_child, "Sp_rot90", "support_point"),
    "curve of three points": (_curve_of_three_points, "Sc_z", "support_curve"),
    "other line orientation": (_other_line_orientation, "Sc_x_rot", "line_orientation"),
    "support outside structures": (_support_elsewhere, "Sp_rot90", "support_point"),
    "unknown support kind": (_unknown_support_kind, "Sp_rot90", "support_surface"),
}


@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_a_construct_without_a_concept_field_is_refused_by_name(example_files, tmp_path, case):
    edit, name, keyword = REFUSALS[case]
    _, part, report = _read(_edit(example_files, tmp_path, "genie_supports_frames.xml", edit))
    findings = _findings(report)
    assert findings.pop(name) == [(keyword, 1)]
    # the fixture's own four, and nothing else
    assert sorted(findings) == ["PD_rot in load case LC2", "Sc_slope", "Sp_presc_rot", "Srl_sdx"]
    c = part.concept_fem.constraints
    assert name not in {**c.point_constraints, **c.curve_constraints, **c.rigid_links}


def test_a_curve_over_a_curved_edge_is_refused(example_files, tmp_path, monkeypatch):
    """Read as its two guide points, a curve over an arc would be its chord. No GeniE version
    here made a curved support curve, so the SAT edge is replaced by a circle."""
    from ada.cadit.sat.store import SatReaderFactory
    from ada.geom.curves import Circle

    real = SatReaderFactory.get_named_edge_curve
    edge = _support(ET.parse(example_files / "fem_files/sesam/genie_supports_frames.xml").getroot(), "Sc_z")
    ref = edge.find("geometry/wire/sat_reference/edge").get("edge_ref")

    def arc_at_sc_z(self, name):
        return Circle(None, 1.0) if name == ref else real(self, name)

    monkeypatch.setattr(SatReaderFactory, "get_named_edge_curve", arc_at_sc_z)
    _, part, report = _read(_copy(example_files, tmp_path, "genie_supports_frames.xml"))
    assert _findings(report)["Sc_z"] == [("support_curve", 1)]
    assert "Sc_z" not in part.concept_fem.constraints.curve_constraints
    assert "Sc_negz" in part.concept_fem.constraints.curve_constraints


# --- round trip --------------------------------------------------------------------------------


def _state(a: ada.Assembly) -> dict:
    """Every support and prescribed displacement, as plain values."""
    out = {}
    (part,) = a.get_all_subparts()
    c = part.concept_fem.constraints

    def dofs(concept):
        return [(d.dof, d.constraint_type, d.spring_stiffness) for d in concept.dof_constraints]

    for name, sp in c.point_constraints.items():
        out[name] = ("point", _p(sp.position), dofs(sp))
    for name, sc in c.curve_constraints.items():
        out[name] = ("curve", _p(sc.start_pos), _p(sc.end_pos), dofs(sc))
    for name, rl in c.rigid_links.items():
        region = rl.influence_region
        out[name] = (
            "rigid link",
            _p(rl.master_point),
            _p(region.lower_corner),
            _p(region.upper_corner),
            dofs(rl),
            rl.rotation_dependent,
            rl.include_all_edges,
        )
    for lc in part.concept_fem.loads.load_cases.values():
        for ld in lc.loads:
            if isinstance(ld, LoadConceptPrescribedDisplacement):
                out[ld.name] = ("prescribed", lc.name, ld.support.name, ld.translation, ld.rotation)
    return out


@pytest.mark.parametrize("fixture", ["genie_supports_all_kinds.xml", "genie_supports_frames.xml"])
def test_read_write_read_is_identity(example_files, tmp_path, fixture):
    first, _, _ = _read(_copy(example_files, tmp_path, fixture))
    written = tmp_path / "written" / "written.xml"
    written.parent.mkdir()
    first.to_genie_xml(written)
    second, _, report = _read(written)
    assert _findings(report) == {}
    assert _state(second) == _state(first)
    # not vacuous: every kind is in it
    kinds = {v[0] for v in _state(first).values()}
    assert kinds == {"point", "curve", "rigid link", "prescribed"}


def test_the_writer_writes_a_rigid_links_own_options(tmp_path):
    """Every rigid link was written include_all_edges="true" rotation_dependent="true" whatever it
    held; GeniE V8.13 imports rotation_dependent="true" as slave_r*="dependent"."""
    from ada.fem.concept.constraints import (
        ConstraintConceptDofType,
        ConstraintConceptRigidLink,
        RigidLinkRegion,
    )

    p = ada.Part("P") / ada.Plate.from_3d_points("Pl", [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], 0.01)
    for name, rot, edges in [("a", False, True), ("b", True, False)]:
        p.concept_fem.constraints.add_rigid_link(
            ConstraintConceptRigidLink(
                name,
                (0.5, 0.5, 1),
                RigidLinkRegion((0, 0, -0.1), (1, 1, 0.1)),
                ConstraintConceptDofType.encastre(),
                rotation_dependent=rot,
                include_all_edges=edges,
            )
        )
    xml = tmp_path / "rl.xml"
    (ada.Assembly("A") / p).to_genie_xml(xml)
    written = {el.get("name"): el.attrib for el in ET.parse(xml).getroot().iter("support_rigid_link")}
    assert (written["a"]["rotation_dependent"], written["a"]["include_all_edges"]) == ("false", "true")
    assert (written["b"]["rotation_dependent"], written["b"]["include_all_edges"]) == ("true", "false")


# --- the user's model --------------------------------------------------------------------------


@pytest.mark.skipif(not USER_MODEL.is_file(), reason="the user's GeniE workspace is not on this machine")
def test_the_user_model_reads_its_four_rigid_link_supports():
    """Under the support columns, at the centre of each pad (z = -1.95), each linking the pad's
    0.65 x 0.65 m bottom face with slave rotations dependent. adapy read "no supports" here."""
    with conversion_report.collect() as report:
        a = ada.from_gnx(USER_MODEL)
    links = {}
    for part in a.get_all_parts_in_assembly(include_self=True):
        constraints = part.concept_fem.constraints
        assert constraints.point_constraints == {} and constraints.curve_constraints == {}
        links.update(constraints.rigid_links)
    held = {
        "Mini_sup_sw_sup1_c1": ((4.225, 0.325), {"dx": "fixed", "dy": "fixed", "dz": "fixed"}),
        "Mini_sup_se_sup2_c1": ((11.375, 0.325), {"dy": "fixed", "dz": "fixed"}),
        "Mini_sup_nw_sup3_c1": ((4.225, 10.075), {"dz": "fixed"}),
        "Mini_sup_ne_sup4_c1": ((11.375, 10.075), {"dz": "fixed"}),
    }
    assert sorted(links) == sorted(held)
    for name, ((x, y), dofs) in held.items():
        rl = links[name]
        assert _p(rl.master_point) == pytest.approx((x, y, -1.95)), name
        assert _p(rl.influence_region.lower_corner) == pytest.approx((x - 0.325, y - 0.325, -1.95)), name
        assert _p(rl.influence_region.upper_corner) == pytest.approx((x + 0.325, y + 0.325, -1.95)), name
        assert _dofs(rl) == dofs, name
        assert (rl.rotation_dependent, rl.include_all_edges) == (True, True), name
    assert not any(f.keyword.startswith("support") or f.keyword == "local_system" for f in report.findings)
