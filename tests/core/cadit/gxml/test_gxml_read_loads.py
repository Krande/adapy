"""GeniE concept loads read into ``Part.concept_fem.loads``, checked against GeniE's own mesh.

The fixtures are GeniE V9.2-01 exports of three probe models, unchanged but for the user name:

* ``genie_loads_all_kinds.xml`` -- 16 load cases, one per load kind, and ``LCC1 = 1.5 LC_ll_beam_u
  + 1.0 LC_grav``, exported after meshing.
* ``genie_loads_reparam.xml`` -- a point load off a mesh node, a partial line footprint, and a
  line load and a point load each given in a rotated ``LocalSystem``.
* ``genie_loads_segments.xml`` -- beam footprints on a beam of two unequal segments, a rotation
  field and a placed equipment.

The resultants below are the ones GeniE's own ``T1.FEM`` for each model carries (summed from its
BNLOAD/BELOAD1/BELLO2/BEUSLO records), so a reading that loads the wrong place, the wrong length or
the wrong side of a plate shows up as a wrong number here.
"""

from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET

import numpy as np
import pytest

import ada
from ada.fem.concept.loads import (
    LoadConceptAccelerationField,
    LoadConceptLine,
    LoadConceptPoint,
    LoadConceptSurface,
)
from ada.fem.formats import conversion_report

STAGE = "genie xml reader"

#: Resultant force per load case in GeniE's T1.FEM for ``genie_loads_all_kinds.xml``
#: (LC_pt_beam also carries the moment My=500 as a nodal moment).
ALL_KINDS_RESULTANTS = {
    "LC_pt_beam": (0.0, 0.0, -10000.0),
    "LC_pt_beampoint": (0.0, 0.0, -2000.0),
    "LC_pt_plate": (0.0, 0.0, -5000.0),
    "LC_ll_beam_u": (0.0, 0.0, -4000.0),
    "LC_ll_beam_v": (0.0, 0.0, -8000.0),
    "LC_ll_beam_part": (0.0, 0.0, -4000.0),
    "LC_ll_beam_local": (0.0, 0.0, -4000.0),
    "LC_ll_line": (0.0, 0.0, -6000.0),
    "LC_ll_plate_edge": (0.0, 0.0, -500.0),
    "LC_press_u": (0.0, 0.0, -4000.0),
    "LC_press_back": (0.0, 0.0, 4000.0),
}

#: The load cases whose one load is refused, and the construct it is refused under.
ALL_KINDS_REFUSED = {
    "LC_press_3pt": "pressure2d_3point_varying",
    "LC_press_lin": "pressure2d_linear_function",
    "LC_comp": "component2d_constant",
    "LC_poly": "footprint_polygon",
}


def _read(example_files, tmp_path, fixture):
    # Read a copy: the reader writes a .sat beside the XML it is given.
    src = example_files / "fem_files" / "sesam" / fixture
    dst = tmp_path / fixture
    shutil.copy(src, dst)
    with conversion_report.collect() as report:
        a = ada.from_genie_xml(dst)
    (part,) = a.get_all_subparts()
    return part, report


def _findings(report):
    return {(f.kind, f.keyword): f for f in report.findings if f.stage == STAGE}


def _resultant(lc) -> tuple[np.ndarray, np.ndarray]:
    """Total force and total applied moment of a concept load case, from its concept loads."""
    force = np.zeros(3)
    moment = np.zeros(3)
    for load in lc.loads:
        if isinstance(load, LoadConceptPoint):
            force += load.force
            moment += load.moment
        elif isinstance(load, LoadConceptLine):
            length = np.linalg.norm(np.asarray(load.end_point, float) - np.asarray(load.start_point, float))
            force += length * (np.asarray(load.intensity_start) + np.asarray(load.intensity_end)) / 2
        elif isinstance(load, LoadConceptSurface):
            # a pressure pushes into the side it is on: -n on the front face, +n on the back
            normal = np.asarray(load.plate_ref.normal, float)
            sign = -1.0 if load.side == "front" else 1.0
            force += sign * load.pressure * load.plate_ref.poly.get_area() * normal
        elif not isinstance(load, LoadConceptAccelerationField):
            raise TypeError(type(load))
    return force, moment


def _p(point) -> tuple:
    return tuple(float(c) for c in point)


# --- genie_loads_all_kinds.xml ----------------------------------------------------------------


@pytest.fixture
def all_kinds(example_files, tmp_path):
    return _read(example_files, tmp_path, "genie_loads_all_kinds.xml")


def test_every_load_case_is_read_with_its_number(all_kinds):
    part, _ = all_kinds
    cases = part.concept_fem.loads.load_cases
    assert list(cases) == [
        "LC_pt_beam",
        "LC_pt_beampoint",
        "LC_pt_plate",
        "LC_ll_beam_u",
        "LC_ll_beam_v",
        "LC_ll_beam_part",
        "LC_ll_beam_local",
        "LC_ll_line",
        "LC_ll_plate_edge",
        "LC_press_u",
        "LC_press_back",
        "LC_press_3pt",
        "LC_press_lin",
        "LC_comp",
        "LC_poly",
        "LC_grav",
    ]
    assert [lc.fem_loadcase_number for lc in cases.values()] == list(range(1, 17))
    assert all(lc.invalidated is False and lc.mesh_loads_as_mass is False for lc in cases.values())


def test_point_loads(all_kinds):
    part, _ = all_kinds
    cases = part.concept_fem.loads.load_cases

    mom, pl = cases["LC_pt_beam"].loads
    assert isinstance(mom, LoadConceptPoint) and isinstance(pl, LoadConceptPoint)
    assert (mom.name, _p(mom.position), mom.force, mom.moment) == (
        "PL_beam_mom",
        (1.0, 0.0, 0.0),
        (0.0, 0.0, 0.0),
        (0.0, 500.0, 0.0),
    )
    assert (pl.name, _p(pl.position), pl.force, pl.moment) == (
        "PL_beam",
        (2.0, 0.0, 0.0),
        (0.0, 0.0, -10000.0),
        (0.0, 0.0, 0.0),
    )

    # FootprintBeamPoint(Bm1, 0.25): GeniE put its BNLOAD on the node at x=1 of 0..4
    (bp,) = cases["LC_pt_beampoint"].loads
    assert isinstance(bp, LoadConceptPoint)
    assert (_p(bp.position), bp.force) == ((1.0, 0.0, 0.0), (0.0, 0.0, -2000.0))

    # a point on the plate (GeniE: footprint_plate_point Pl1, BNLOAD on node 51 at (2, 3.5, 0))
    (pp,) = cases["LC_pt_plate"].loads
    assert isinstance(pp, LoadConceptPoint)
    assert (_p(pp.position), pp.force) == ((2.0, 3.5, 0.0), (0.0, 0.0, -5000.0))
    assert {mom.system, pl.system, bp.system, pp.system} == {"local"}


@pytest.mark.parametrize(
    "case, name, start, end, q1, q2",
    [
        ("LC_ll_beam_u", "LL_beam_u", (0, 0, 0), (4, 0, 0), (0, 0, -1000), (0, 0, -1000)),
        # linearly varying: both ends kept
        ("LC_ll_beam_v", "LL_beam_v", (0, 1.5, 0), (4, 1.5, 0), (0, 0, -1000), (0, 0, -3000)),
        # FootprintBeam(Bm2, 0.25, 0.75): GeniE loaded the elements from x=1 to x=3
        ("LC_ll_beam_part", "LL_beam_part", (1, 1.5, 0), (3, 1.5, 0), (0, 0, -2000), (0, 0, -2000)),
        # local_system="relative" on the vertical Bm3: GeniE kept fz global (q=(0,0,-1000), axial)
        ("LC_ll_beam_local", "LL_beam_local", (0, 6, 0), (0, 6, 4), (0, 0, -1000), (0, 0, -1000)),
        ("LC_ll_line", "LL_line", (0, 0, 0), (4, 0, 0), (0, 0, -1500), (0, 0, -1500)),
        ("LC_ll_plate_edge", "LL_plate_edge", (4, 3, 0), (4, 4, 0), (0, 0, -500), (0, 0, -500)),
    ],
)
def test_line_loads(all_kinds, case, name, start, end, q1, q2):
    part, _ = all_kinds
    (load,) = part.concept_fem.loads.load_cases[case].loads
    assert isinstance(load, LoadConceptLine)
    assert load.name == name
    assert (_p(load.start_point), _p(load.end_point)) == (_p(start), _p(end))
    assert (load.intensity_start, load.intensity_end) == (_p(q1), _p(q2))
    assert load.system == "local"


@pytest.mark.parametrize(
    "case, name, side", [("LC_press_u", "SL_press_u", "front"), ("LC_press_back", "SL_press_back", "back")]
)
def test_plate_pressures_keep_their_side(all_kinds, case, name, side):
    part, _ = all_kinds
    (load,) = part.concept_fem.loads.load_cases[case].loads
    assert isinstance(load, LoadConceptSurface)
    assert (load.name, load.plate_ref.name, load.pressure, load.side, load.points) == (name, "Pl1", 1000.0, side, None)
    # the side is only meaningful against the plate's normal, which the XML states as +z
    assert _p(load.plate_ref.normal) == (0.0, 0.0, 1.0)


@pytest.mark.parametrize("case", sorted(ALL_KINDS_RESULTANTS))
def test_resultants_equal_genies_mesh(all_kinds, case):
    part, _ = all_kinds
    force, moment = _resultant(part.concept_fem.loads.load_cases[case])
    np.testing.assert_array_equal(force, ALL_KINDS_RESULTANTS[case])
    np.testing.assert_array_equal(moment, (0.0, 500.0, 0.0) if case == "LC_pt_beam" else (0.0, 0.0, 0.0))


def test_each_refused_kind_is_one_named_finding_and_nothing_else(all_kinds):
    part, report = all_kinds
    cases = part.concept_fem.loads.load_cases
    for case in ALL_KINDS_REFUSED:
        assert cases[case].loads == [], case

    found = _findings(report)
    assert set(found) == {("omitted", kw) for kw in ALL_KINDS_REFUSED.values()}
    for case, keyword in ALL_KINDS_REFUSED.items():
        finding = found[("omitted", keyword)]
        assert finding.count == 1
        assert case in finding.subject


def test_only_the_self_weight_case_gets_gravity(all_kinds, example_files):
    """GeniE writes a gravity_load for every load case after meshing; only one is gravity."""
    root = ET.parse(example_files / "fem_files/sesam/genie_loads_all_kinds.xml").getroot()
    gravity = root.findall("./model/analysis_domain/analyses/global/loads/environmental_loads/gravity_load")
    assert (len(gravity), sum(g.get("include_selfweight") == "true" for g in gravity)) == (16, 1)

    part, _ = all_kinds
    cases = part.concept_fem.loads.load_cases
    fields = {name: [x for x in lc.loads if isinstance(x, LoadConceptAccelerationField)] for name, lc in cases.items()}
    assert {name for name, f in fields.items() if f} == {"LC_grav"}
    (g,) = fields["LC_grav"]
    assert (g.acceleration, g.include_self_weight, g.rotational_field) == ((0.0, 0.0, -9.80665), True, None)
    assert [name for name, lc in cases.items() if lc.include_self_weight] == ["LC_grav"]


def test_the_combination_under_analysis_is_read_with_its_factors(all_kinds):
    part, _ = all_kinds
    loads = part.concept_fem.loads
    assert list(loads.load_case_combinations) == ["LCC1"]
    lcc = loads.load_case_combinations["LCC1"]
    assert [(t.load_case, t.factor, t.phase) for t in lcc.load_cases] == [
        (loads.load_cases["LC_ll_beam_u"], 1.5, 0),
        (loads.load_cases["LC_grav"], 1.0, 0),
    ]
    assert (lcc.global_scale_factor, lcc.convert_load_to_mass, lcc.complex_type) == (1.0, False, "static")


# --- genie_loads_reparam.xml -------------------------------------------------------------------


def test_reparam_loads_and_the_rotated_coordinate_system_refusal(example_files, tmp_path):
    part, report = _read(example_files, tmp_path, "genie_loads_reparam.xml")
    cases = part.concept_fem.loads.load_cases

    (ll1,) = cases["LC1"].loads
    assert (_p(ll1.start_point), _p(ll1.end_point), ll1.intensity_start) == (
        (0.0, 0.0, 0.0),
        (4.0, 0.0, 0.0),
        (0.0, 0.0, -1000.0),
    )
    (pl1,) = cases["LC2"].loads
    assert (_p(pl1.position), pl1.force) == ((2.0, 0.0, 0.0), (0.0, 0.0, -10000.0))
    # off a mesh node: GeniE meshed it as a 5 mm BELOAD1 (Fz=-9999.96 from the digits it wrote)
    (pl2,) = cases["LC3"].loads
    assert (_p(pl2.position), pl2.force) == ((1.3, 1.5, 0.0), (0.0, 0.0, -10000.0))
    # the partial FootprintLine 1.3..2.9 m: GeniE's BELOAD1s total Fz=-1600
    (ll2,) = cases["LC4"].loads
    assert (_p(ll2.start_point), _p(ll2.end_point)) == ((1.3, 1.5, 0.0), (2.9, 1.5, 0.0))
    # 2.9 - 1.3 is 1.5999999999999999 in binary; the 1e-12 is that, not a tolerance on the reading
    assert _resultant(cases["LC4"])[0][2] == pytest.approx(-1600.0, rel=1e-12)
    np.testing.assert_array_equal(_resultant(cases["LC1"])[0], (0.0, 0.0, -4000.0))

    # both loads with LocalSystem(x=(0,1,0), z=(0,0,1)) are refused: one finding, counted twice
    assert cases["LC5"].loads == [] and cases["LC6"].loads == []
    found = _findings(report)
    assert set(found) == {("omitted", "coordinate_system")}
    finding = found[("omitted", "coordinate_system")]
    assert finding.count == 2
    assert {finding.subject, *finding.other_subjects} == {"LL3 in load case LC5", "PL3 in load case LC6"}


# --- genie_loads_segments.xml -----------------------------------------------------------------


def test_beam_parameters_run_over_the_whole_segmented_beam(example_files, tmp_path):
    """Bm5 is 0..4 split at x=1. GeniE put param=0.5 on the node at x=2, and 0.25..0.75 on x=1..3."""
    part, report = _read(example_files, tmp_path, "genie_loads_segments.xml")
    cases = part.concept_fem.loads.load_cases

    (pl,) = cases["LC1"].loads
    assert (_p(pl.position), pl.force) == ((2.0, 10.0, 0.0), (0.0, 0.0, -1000.0))
    (ll,) = cases["LC2"].loads
    assert (_p(ll.start_point), _p(ll.end_point)) == ((1.0, 10.0, 0.0), (3.0, 10.0, 0.0))
    # GeniE's T1.FEM: LC1 Fz=-1000 (one BNLOAD), LC2 Fz=-2000 (four BELOAD1)
    np.testing.assert_array_equal(_resultant(cases["LC1"])[0], (0.0, 0.0, -1000.0))
    np.testing.assert_array_equal(_resultant(cases["LC2"])[0], (0.0, 0.0, -2000.0))

    # the rotation field and the placed equipment: one named finding each, nothing read
    assert cases["LC3"].loads == [] and cases["LC4"].loads == []
    found = _findings(report)
    assert set(found) == {("omitted", "rotation_field"), ("omitted", "placed_shape")}
    assert found[("omitted", "rotation_field")].count == 1
    assert found[("omitted", "placed_shape")].count == 1
    assert found[("omitted", "placed_shape")].subject == "Eq1 in load case LC4"


# --- the round trip through adapy's own GeniE writer ------------------------------------------


def _dump(part) -> dict:
    """Every field of the concept loads, as plain values, so two readings can be compared."""

    def load_key(load):
        if isinstance(load, LoadConceptPoint):
            return ("point", load.name, _p(load.position), load.force, load.moment, load.system)
        if isinstance(load, LoadConceptLine):
            return (
                "line",
                load.name,
                _p(load.start_point),
                _p(load.end_point),
                load.intensity_start,
                load.intensity_end,
                load.system,
            )
        if isinstance(load, LoadConceptSurface):
            return ("surface", load.name, load.plate_ref.name, load.points, load.pressure, load.side, load.system)
        if isinstance(load, LoadConceptAccelerationField):
            return ("acc", load.name, load.acceleration, load.include_self_weight, load.rotational_field)
        raise TypeError(type(load))

    loads = part.concept_fem.loads
    cases = {
        name: (
            lc.fem_loadcase_number,
            lc.design_condition,
            lc.complex_type,
            lc.invalidated,
            lc.include_self_weight,
            lc.mesh_loads_as_mass,
            [load_key(x) for x in lc.loads],
        )
        for name, lc in loads.load_cases.items()
    }
    combos = {
        name: (
            lcc.design_condition,
            lcc.global_scale_factor,
            lcc.convert_load_to_mass,
            [(t.load_case.name, t.factor, t.phase) for t in lcc.load_cases],
        )
        for name, lcc in loads.load_case_combinations.items()
    }
    return {"cases": cases, "combinations": combos}


@pytest.mark.parametrize(
    "fixture", ["genie_loads_all_kinds.xml", "genie_loads_reparam.xml", "genie_loads_segments.xml"]
)
def test_round_trip_through_the_genie_writer(example_files, tmp_path, fixture):
    part, _ = _read(example_files, tmp_path, fixture)
    first = _dump(part)
    assert any(lc[-1] for lc in first["cases"].values())

    out = tmp_path / "round_trip" / "rt.xml"
    part.get_assembly().to_genie_xml(out)
    with conversion_report.collect() as report:
        (again,) = ada.from_genie_xml(out).get_all_subparts()

    assert _dump(again) == first
    # the refused loads were never in the model, so nothing is left to refuse the second time
    assert _findings(report) == {}
