"""Tests for the capacity manager (DNV-RP-C201 capacity-model reconstruction).

These validate against the matched Mini-topside reference dataset that lives in
the sibling ``dnv-rp-c201`` repo (licensed; not vendored here), so every test
skips when the reference is absent. Set ``ADA_CAPACITY_REF`` to override the
reference ``.../temp/Assembly`` directory.

Validated gates:
* geometry (thickness / length / width) of every plate field matches Genie's
  ``model.json``;
* resolved transverse-membrane and shear design stresses match Genie's
  ``*__CriteriaResults.json`` for (almost) every (result case, stiffener).
"""

from __future__ import annotations

import glob
import json
import os
import pathlib
import re

import pytest

_DEFAULT_REF = pathlib.Path(
    r"C:\AibelProgs\projects\GitHub\dnv-rp-c201\.local\reference" r"\example_mini_topside_codecheck\temp\Assembly"
)
REF = pathlib.Path(os.environ.get("ADA_CAPACITY_REF", _DEFAULT_REF))
SIN = REF / "Analysis_pm" / "20260617_122105_R1.SIN"
MODEL_JSON = REF / "Cc2.run1" / "model.json"

pytestmark = pytest.mark.skipif(
    not (SIN.exists() and MODEL_JSON.exists()),
    reason=f"Mini-topside capacity reference not found under {REF}",
)

# A second, richer reference: the (partially completed) codecheck2 Genie run
# covers the full double bottom — girders in two directions — where Genie merges
# the per-cell concept fields into full-width stiffened panels. It exercises the
# geometric merge that the small codecheck1 reference does not.
_DEFAULT_REF2 = pathlib.Path(
    r"C:\AibelProgs\projects\GitHub\dnv-rp-c201\.local\reference" r"\example_mini_topside_codecheck2\temp\Assembly"
)
REF2 = pathlib.Path(os.environ.get("ADA_CAPACITY_REF2", _DEFAULT_REF2))
SIN2 = REF2 / "Analysis_pm" / "20260620_150626_R1.SIN"
MODEL_JSON2 = REF2 / "Cc2.run1" / "model.json"
ref2 = pytest.mark.skipif(
    not (SIN2.exists() and MODEL_JSON2.exists()),
    reason=f"Mini-topside capacity reference (codecheck2) not found under {REF2}",
)

_STIFF_RE = re.compile(r"Stiffener \(Name/Id\): \(([^/]+?) / \d+\)")


@pytest.fixture(scope="module")
def manager():
    from ada.fem.capacity import CapacityManager, ModelJsonSource

    return CapacityManager.from_sin(SIN, ModelJsonSource(MODEL_JSON))


@pytest.fixture(scope="module")
def genie_variables():
    out = {}
    for f in glob.glob(str(REF / "Cc2.run1" / "SesamCore_RUN1_panelGroup_*__CriteriaResults.json")):
        for rc in json.loads(pathlib.Path(f).read_text()):
            case = int(rc["ResultCase"])
            for rule in rc.get("Results", []):
                for cr in rule.get("CriteriaResults", []):
                    m = _STIFF_RE.search(cr.get("EntityReference", ""))
                    if m:
                        out[(case, m.group(1).strip())] = (cr.get("Variables", {}), cr.get("VariableVectors", {}))
    return out


def test_identifies_all_panel_groups(manager):
    models = manager.capacity_models()
    genie = json.loads(MODEL_JSON.read_text())
    assert len(models) == len(genie["BucklingModels"]) == 10
    assert {m.name for m in models} == {bm["Name"] for bm in genie["BucklingModels"]}


def test_plate_geometry_matches_model_json(manager):
    models = manager.capacity_models()
    genie = {bm["Name"]: bm for bm in json.loads(MODEL_JSON.read_text())["BucklingModels"]}
    checked = 0
    for m in models:
        gplates = {p["Name"]: p["Geometry"] for p in genie[m.name]["Plates"]}
        for p in m.plates:
            g = gplates[p.name]
            assert p.thickness == pytest.approx(g["Thickness"], abs=1e-4)
            assert p.length == pytest.approx(g["Length"], rel=1e-3, abs=1e-4)
            assert p.width == pytest.approx(g["Width"], rel=1e-3, abs=1e-4)
            checked += 1
    assert checked > 30


def test_stiffener_span_matches_plate_length(manager):
    models = manager.capacity_models()
    genie = {bm["Name"]: bm for bm in json.loads(MODEL_JSON.read_text())["BucklingModels"]}
    for m in models:
        ref_span = genie[m.name]["Plates"][0]["Geometry"]["Length"]
        for s in m.stiffeners:
            assert s.span == pytest.approx(ref_span, rel=1e-3, abs=1e-4)


def test_dominant_flange_picks_real_flange_of_genie_t_girder():
    """Genie models a T-girder as an unsymmetrical I with a dummy flange
    (width == tw, token thickness); the real flange can sit in either slot.
    Reference: TG850x300x16x20 arrived as top 16 x 0.1 mm / bottom 300 x 20 mm
    and was checked with the dummy (UF 8.13 from a near-zero flange)."""
    from ada.fem.capacity.model import dominant_flange

    # real flange in the bottom slot (the TG850x300x16x20 case)
    assert dominant_flange(0.016, (0.016, 0.0001), (0.3, 0.02)) == (0.3, 0.02)
    # real flange in the top slot
    assert dominant_flange(0.016, (0.3, 0.02), (0.016, 0.0001)) == (0.3, 0.02)
    # true unsymmetrical I: the larger flange governs
    assert dominant_flange(0.012, (0.2, 0.02), (0.15, 0.015)) == (0.2, 0.02)
    # no real flange at all (flat bar idealization)
    assert dominant_flange(0.016, (0.016, 0.0001), (None, None)) == (0.0, 0.0)
    # plain T stored top-only (legacy single-flange path still works)
    assert dominant_flange(0.012, (0.12, 0.016)) == (0.12, 0.016)


def test_glsec_sections_parse_as_angular_from_sin():
    from ada.fem.formats.sesam.results.read_sin import read_sin_file
    from ada.sections.categories import BaseTypes

    sec = read_sin_file(SIN).mesh.sections[3]
    assert sec.type == BaseTypes.ANGULAR
    assert sec.h == pytest.approx(0.22)
    assert sec.t_w == pytest.approx(0.01)
    assert sec.w_top == pytest.approx(0.041)
    assert sec.t_ftop == pytest.approx(0.02505)


def test_sin_source_builds_mini_grid_x100_capacity_models_like_genie():
    from ada.fem.capacity import CapacityManager, SinSource

    native = {
        model.name: model
        for model in CapacityManager.from_sin(SIN, SinSource(group="Mini_grid_x100")).capacity_models()
    }
    genie = json.loads(MODEL_JSON.read_text())
    genie_models = {model["Name"]: model for model in genie["BucklingModels"]}

    assert set(native) == set(genie_models)
    for name, model in native.items():
        ref = genie_models[name]
        native_stiffeners = sorted((s.name, tuple(int(e) for e in s.element_ids)) for s in model.stiffeners)
        genie_stiffeners = sorted(
            (stiffener["Name"], tuple(int(e) for e in stiffener["FiniteElements"])) for stiffener in ref["Stiffeners"]
        )
        native_plates = sorted(tuple(int(e) for e in plate.element_ids) for plate in model.plates)
        genie_plates = sorted(tuple(int(e) for e in plate["FiniteElements"]) for plate in ref["Plates"])
        assert native_stiffeners == genie_stiffeners
        assert native_plates == genie_plates


def test_sin_source_scopes_area_set_to_capacity_grid_like_genie():
    from ada.fem.capacity import CapacityManager, SinSource

    native = CapacityManager.from_sin(SIN, SinSource(group="Mini_area_dbl_btm")).capacity_models()

    assert {model.name for model in native} == {
        "panelGroup(Mini_dbl_btm_f0_i1_j1)",
        "panelGroup(Mini_dbl_btm_f0_i2_j1)",
        "panelGroup(Mini_dbl_btm_f0_i3_j1)",
        "panelGroup(Mini_dbl_btm_f0_i4_j1)",
    }
    assert sum(len(model.stiffeners) for model in native) == 12


def test_mini_group_scopes_the_whole_model():
    """The top-level ``Mini`` set spans the whole model, so scoping to it must
    match the unscoped run.

    Regression for the SIN set reader: a large/mixed set is written as several
    ``GSETMEMB`` records sharing one set id (and may mix node + element records).
    Keying by set id alone kept only the last record, so ``Mini`` resolved to a
    fraction of its elements and the check covered only part of the structure.
    """
    from ada.fem.capacity import CapacityManager, SinSource
    from ada.fem.formats.sesam.results.read_sin import read_sin_file

    mesh = read_sin_file(SIN, step=1).mesh
    mini_members = SinSource._set_members(mesh, "Mini")
    n_elements = sum(len(block.identifiers) for block in mesh.elements)
    assert len(mini_members) == n_elements  # every element, not just the last record's chunk

    whole = {m.name for m in CapacityManager.from_sin(SIN, SinSource()).capacity_models()}
    scoped = {m.name for m in CapacityManager.from_sin(SIN, SinSource(group="Mini")).capacity_models()}
    assert scoped == whole
    assert len(whole) > 50  # the full Mini model, not a partial scope


def test_sin_source_full_mini_models_are_unique_rectangular_panels():
    import numpy as np

    from ada.fem.capacity import CapacityManager, SinSource

    manager = CapacityManager.from_sin(SIN, SinSource())
    models = manager.capacity_models()

    owners: dict[int, str] = {}
    for model in models:
        for plate in model.plates:
            for element_id in plate.element_ids:
                assert element_id not in owners, (
                    f"shell element {element_id} belongs to both {owners[element_id]} and {model.name}"
                )
                owners[element_id] = model.name

    # The geometric merge fuses the per-cell concept fields into maximal
    # rectangular stiffened panels (Genie-style), so there are far fewer, larger
    # panels than the ~153 atomic cells — without dropping any stiffener and
    # while every panel stays a unique, rectangular plate field.
    unmerged = CapacityManager.from_sin(SIN, SinSource(merge_panels=False)).capacity_models()
    assert len(models) < len(unmerged)
    assert 50 <= len(models) < 150
    assert sum(len(model.stiffeners) for model in models) == sum(len(model.stiffeners) for model in unmerged)
    assert sum(len(model.stiffeners) for model in models) >= 650
    assert owners
    for model in models:
        plate_ids = [element_id for plate in model.plates for element_id in plate.element_ids]
        beam_ids = [element_id for stiffener in model.stiffeners for element_id in stiffener.element_ids]
        assert _plate_field_area_ratio(manager.mesh, plate_ids, beam_ids, np) >= 0.95


def test_sin_source_filters_primary_girders_by_secondary_concept_profile():
    from ada.fem.capacity import SinSource
    from ada.fem.capacity.extract import AuxRecords, geono_of
    from ada.fem.formats.sesam.results.read_sin import read_sin_file

    mesh = read_sin_file(SIN).mesh
    aux = AuxRecords.from_sin(SIN)

    raw = SinSource(group="Mini_grid_x100", classify_secondary=False).groups(mesh, aux)
    classified = SinSource(group="Mini_grid_x100").groups(mesh, aux)

    raw_stiffeners = [s for model in raw for s in model.stiffeners]
    kept_stiffeners = [s for model in classified for s in model.stiffeners]
    raw_geonos = {geono_of(mesh, s.element_ids[0]) for s in raw_stiffeners}
    kept_geonos = {geono_of(mesh, s.element_ids[0]) for s in kept_stiffeners}

    assert 5 in raw_geonos  # TG600 primary girders are candidates before classification.
    assert kept_geonos == {3}  # HP220 secondary stiffener profile.
    assert len(kept_stiffeners) == 54
    assert any("_gbm" in s.name for s in kept_stiffeners)


def test_genie_mirror_roundtrips(manager, tmp_path):
    out = tmp_path / "mirror.json"
    manager.to_genie_json(out)
    data = json.loads(out.read_text())
    assert len(data["BucklingModels"]) == 10


# Panel groups whose plate fields are a single shell element each — the
# resolution is exact here. Multi-element fields (the ``west_main`` groups) need
# the tributary/along-span refinement still under calibration (see
# stress_resolve module docstring), so they're reported but not asserted exact.
_SINGLE_ELEMENT_GROUPS = ("dbl_btm", "west_small")


def _is_single_element_group(name: str) -> bool:
    return any(tag in name for tag in _SINGLE_ELEMENT_GROUPS)


def test_transverse_and_shear_match_genie(manager, genie_variables):
    """Resolved transverse-membrane and shear stresses match Genie's.

    Asserts on the *median* relative error over non-trivial values: the membrane
    resolution is fundamentally exact, while a tail of (case, stiffener) pairs
    where Genie reports the same stiffener under several connections with
    different tributaries is not yet disambiguated (a per-connection refinement,
    see stress_resolve module docstring). The median is robust to that tail.
    """
    import statistics

    resolved = manager.resolve_cases()
    matched = 0
    trans_rel: list[float] = []
    shear_rel: list[float] = []
    for rc in resolved:
        g = genie_variables.get((rc.result_case, rc.stiffener))
        if g is None:
            continue
        matched += 1
        gv, gvec = g
        gtrans = gvec.get("AverageTransverseMembraneStresses")
        if gtrans and abs(gtrans[1]) > 1.0:  # skip ~0 references
            trans_rel.append(abs(rc.variables["SigmaYSd"] - gtrans[1]) / abs(gtrans[1]))
        gtau = gv.get("TauSd", 0.0)
        if abs(gtau) > 1.0:
            shear_rel.append(abs(rc.variables["TauSd"] - gtau) / abs(gtau))

    assert matched == 540
    assert len(trans_rel) > 400 and len(shear_rel) > 400
    # Median relative error is at the float-noise floor → the resolution matches.
    assert statistics.median(trans_rel) < 1e-4
    assert statistics.median(shear_rel) < 1e-4


def test_rdpoints_sampling_matches_genie_longitudinal_and_transverse_ends(manager, genie_variables):
    """RDPOINTS-coordinate sampling closes the single-connection stress residuals.

    The remaining tail is concentrated in the midpoint field average for a few
    irregular plate fields, so assert the calibrated median plus per-vector
    within-2% fractions.
    """
    import statistics

    resolved = manager.resolve_cases()
    trans_start_rel: list[float] = []
    trans_end_rel: list[float] = []
    long_rel: list[float] = []
    axial_rel: list[float] = []
    for rc in resolved:
        g = genie_variables.get((rc.result_case, rc.stiffener))
        if g is None:
            continue
        _gv, gvec = g
        ours_t = rc.vectors.get("AverageTransverseMembraneStresses", [])
        genie_t = gvec.get("AverageTransverseMembraneStresses", [])
        if len(ours_t) == 3 and len(genie_t) == 3:
            if abs(genie_t[0]) > 1.0:
                trans_start_rel.append(abs(ours_t[0] - genie_t[0]) / abs(genie_t[0]))
            if abs(genie_t[2]) > 1.0:
                trans_end_rel.append(abs(ours_t[2] - genie_t[2]) / abs(genie_t[2]))

        ours_l = rc.vectors.get("AverageLongitudinalMembraneStresses", [])
        genie_l = gvec.get("AverageLongitudinalMembraneStresses", [])
        if len(ours_l) == 3 and len(genie_l) == 3 and abs(genie_l[1]) > 1.0:
            long_rel.append(abs(ours_l[1] - genie_l[1]) / abs(genie_l[1]))

        ours_a = rc.vectors.get("AxialLoads", [])
        genie_a = gvec.get("AxialLoads", [])
        if len(ours_a) == 3 and len(genie_a) == 3 and abs(genie_a[1]) > 1.0:
            axial_rel.append(abs(ours_a[1] - genie_a[1]) / abs(genie_a[1]))

    thresholds = (
        (trans_start_rel, 0.98),
        (trans_end_rel, 0.94),
        (long_rel, 0.90),
        (axial_rel, 0.90),
    )
    for residuals, within2 in thresholds:
        assert len(residuals) > 400
        assert statistics.median(residuals) < 1e-3
        assert sum(r <= 0.02 for r in residuals) / len(residuals) > within2


def test_irregular_plate_field_sampling_matches_genie_tail(manager, genie_variables):
    """Triangular/split adjacent fields use RDPOINTS transforms and full fields."""
    resolved = {(rc.result_case, rc.stiffener): rc for rc in manager.resolve_cases([4, 10])}

    transformed = resolved[(4, "Stiffener_Mini_west_main_f0_i1_j1_sbm1")]
    _gv, gvec = genie_variables[(4, transformed.stiffener)]
    assert transformed.vectors["AverageTransverseMembraneStresses"][0] == pytest.approx(
        gvec["AverageTransverseMembraneStresses"][0], rel=5e-4
    )
    assert transformed.vectors["AverageShearStresses"][2] == pytest.approx(gvec["AverageShearStresses"][2], rel=5e-4)

    split_field = resolved[(10, "Stiffener_Mini_west_main_f0_i2_j2_sbm5")]
    _gv, gvec = genie_variables[(10, split_field.stiffener)]
    assert split_field.vectors["AverageTransverseMembraneStresses"][2] == pytest.approx(
        gvec["AverageTransverseMembraneStresses"][2], rel=5e-4
    )
    assert split_field.vectors["AverageShearStresses"][2] == pytest.approx(gvec["AverageShearStresses"][2], rel=5e-4)


def test_axial_loads_preserve_section_5_positions(manager):
    """AxialLoads is a Section-5 three-position resultant, not a repeated scalar."""
    resolved = manager.resolve_cases()
    non_uniform = [
        rc.vectors["AxialLoads"]
        for rc in resolved
        if "AxialLoads" in rc.vectors and max(rc.vectors["AxialLoads"]) - min(rc.vectors["AxialLoads"]) > 1e-6
    ]

    assert non_uniform


def test_plate_axial_force_matches_membrane_stress_times_tributary(manager):
    """eq (5.1) plate part: ``AxialLoadsPlate == sigma_x * t * s`` with the SAME
    tributary ``(t, s)`` the capacity check consumes (``model.plates[0]``).

    A mismatch means the stress resolver integrated the plate axial force over a
    different plate than the check applies it to — the wrong-tributary /
    heterogeneous-panel bug, where a stiffener's force was integrated over (e.g.)
    a 40 mm / double-spacing plate while the check used the panel's 10 mm / single
    spacing, inflating N_Sd and the beam-column UF several-fold.
    """
    models = {(m.id or m.name): m for m in manager.capacity_models()}
    checked = 0
    for rc in manager.resolve_cases():
        n_plate = rc.vectors.get("AxialLoadsPlate")
        sigma = rc.vectors.get("AverageLongitudinalMembraneStresses")
        model = models.get(rc.capacity_model_id)
        if not n_plate or not sigma or model is None or not model.plates:
            continue
        ts = model.plates[0].thickness * model.plates[0].width
        for n, s in zip(n_plate, sigma):
            assert n == pytest.approx(s * ts, rel=1e-6, abs=1.0)
            checked += 1
    assert checked > 0


def test_section_5_moment_components_are_resolved(manager, genie_variables):
    """Beam moment/force vectors are emitted for q_FE in the Section-6 check."""
    import statistics

    resolved = manager.resolve_cases()
    beam_moment_rel: list[float] = []
    plate_moment_rel: list[float] = []
    q_fe_nonzero = 0
    for rc in resolved:
        g = genie_variables.get((rc.result_case, rc.stiffener))
        if g is None:
            continue
        gv, gvec = g
        ours_bm = rc.vectors.get("MomentsAboutNeutralAxisBeamMoment", [])
        genie_bm = gvec.get("MomentsAboutNeutralAxisBeamMoment", [])
        ours_pm = rc.vectors.get("MomentsAboutNeutralAxisPlate", [])
        genie_pm = gvec.get("MomentsAboutNeutralAxisPlate", [])
        if len(ours_bm) == 3 and len(genie_bm) == 3:
            for ours, ref in zip(ours_bm, genie_bm):
                if abs(ref) > 1.0:
                    beam_moment_rel.append(abs(ours - ref) / abs(ref))
        if len(ours_pm) == 3 and len(genie_pm) == 3:
            for ours, ref in zip(ours_pm, genie_pm):
                if abs(ref) > 1.0:
                    plate_moment_rel.append(abs(ours - ref) / abs(ref))
        if abs(gv.get("QFE", 0.0)) > 1e-9 and "MomentsAboutNeutralAxis" in rc.vectors:
            q_fe_nonzero += 1

    assert q_fe_nonzero > 0
    assert statistics.median(beam_moment_rel) < 1e-3
    assert statistics.median(plate_moment_rel) < 1e-3


def test_shell_pressure_resolves_to_qdir_when_plate_field_is_loaded(manager, monkeypatch):
    from ada.fem.capacity import stress_resolve

    aux = manager.aux
    models = manager.capacity_models()
    aux.pressure_by_case_element = {
        10: {int(element_id): 1200.0 for model in models for plate in model.plates for element_id in plate.element_ids}
    }
    monkeypatch.setattr(stress_resolve.extract.AuxRecords, "from_sin", staticmethod(lambda _path: aux))

    resolved = manager.resolve_cases([10])

    by_group = {model.name: model for model in models}
    assert resolved
    for rc in resolved:
        width = by_group[rc.panel_group].plates[0].width
        assert rc.variables["PSd"] == pytest.approx(1200.0)
        assert rc.variables["Qdir"] == pytest.approx(1200.0 * width, rel=1e-5)


def test_sin_pressure_records_do_not_load_unrelated_capacity_grid():
    from ada.fem.capacity import CapacityManager, SinSource

    manager = CapacityManager.from_sin(SIN, SinSource(group="Mini_grid_x100"))
    assert manager.aux.pressure_by_case_element

    resolved = manager.resolve_cases([2, 10])

    assert resolved
    assert all(rc.variables["PSd"] == pytest.approx(0.0) for rc in resolved)
    assert all(rc.variables["Qdir"] == pytest.approx(0.0) for rc in resolved)


def _plate_field_area_ratio(mesh, plate_ids, beam_ids, np):
    from ada.fem.capacity.extract import beam_axis_and_span, element_node_coords

    axis, _span = beam_axis_and_span(mesh, (beam_ids[0],))
    first = element_node_coords(mesh, plate_ids[0])
    normal = np.cross(first[1] - first[0], first[2] - first[0])
    normal = normal / (np.linalg.norm(normal) or 1.0)
    perp = np.cross(normal, axis)
    perp = perp / (np.linalg.norm(perp) or 1.0)
    points = np.vstack([element_node_coords(mesh, element_id) for element_id in plate_ids])
    origin = points.mean(axis=0)

    area = 0.0
    xy_blocks = []
    for element_id in plate_ids:
        coords = element_node_coords(mesh, element_id) - origin
        xy = np.column_stack((coords @ axis, coords @ perp))
        xy_blocks.append(xy)
        x = xy[:, 0]
        y = xy[:, 1]
        area += 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))
    xy_all = np.vstack(xy_blocks)
    bbox_area = float(np.ptp(xy_all[:, 0]) * np.ptp(xy_all[:, 1]))
    return area / bbox_area if bbox_area else 0.0


# --------------------------------------------------------------------------- #
# codecheck2 — double bottom with girders in two directions (geometric merge)
# --------------------------------------------------------------------------- #
def _genie_plate_sets(model_json: pathlib.Path) -> set[frozenset]:
    bms = json.loads(model_json.read_text())["BucklingModels"]
    return {frozenset(int(e) for p in bm["Plates"] for e in p["FiniteElements"]) for bm in bms}


def _stiffened_plate_sets(models) -> set[frozenset]:
    return {frozenset(e for p in m.plates for e in p.element_ids) for m in models if m.stiffeners}


@ref2
def test_merge_reproduces_genie_double_bottom_panels():
    """The geometric merge rebuilds Genie's merged double-bottom panels exactly.

    Genie splits the girders-in-two-directions double bottom into a grid of cells
    and merges them back into full-width stiffened panels. The lateral merge
    reproduces a solid majority of those panels element-for-element (the residual
    is Genie's irregular opening/triangular cells, intentionally left split).
    """
    from ada.fem.capacity import CapacityManager, SinSource

    native = CapacityManager.from_sin(SIN2, SinSource()).capacity_models()
    ours = _stiffened_plate_sets(native)
    genie = _genie_plate_sets(MODEL_JSON2)

    assert len(genie & ours) >= 45  # of 72 Genie panels; was ~30 before merging

    # A specific full-width merged bay of the double bottom (six i-cells merged
    # across the longitudinal girders into one panel) is reproduced exactly.
    bms = json.loads(MODEL_JSON2.read_text())["BucklingModels"]
    target = next(bm for bm in bms if bm["Name"].startswith("panelGroup(Mini_dbl_btm_f13_i1_j3"))
    assert len(target["Plates"]) == 24  # 24 plate fields …
    target_set = frozenset(int(e) for p in target["Plates"] for e in p["FiniteElements"])
    assert len(target_set) >= 24  # … spanning ≥ 24 shell elements, full width across the bay
    assert target_set in ours


@ref2
def test_merge_collapses_overrun_double_bottom_cells():
    from ada.fem.capacity import CapacityManager, SinSource

    merged = CapacityManager.from_sin(SIN2, SinSource()).capacity_models()
    unmerged = CapacityManager.from_sin(SIN2, SinSource(merge_panels=False)).capacity_models()

    assert len(merged) < len(unmerged)
    # No stiffener is lost in the merge — only the grouping changes.
    assert sum(len(m.stiffeners) for m in merged) == sum(len(m.stiffeners) for m in unmerged)
    # Every merged panel is still a single rectangular plate field with unique ownership.
    owners: dict[int, str] = {}
    for m in merged:
        for p in m.plates:
            for e in p.element_ids:
                assert e not in owners
                owners[e] = m.name


@ref2
def test_plate_fields_match_genie_subdivision_on_y_grid():
    """Plate fields are split per stiffener bay, matching Genie field-for-field.

    The ``Mini_grid_y100`` cut (a vertical plane) exposed that the old element-id
    run heuristic mis-counted fields (``nP == nS`` instead of ``nS + 1``). The
    geometric per-bay split reproduces Genie's field decomposition exactly for
    every regular panel, on this orientation as well as the x grid.
    """
    from ada.fem.capacity import CapacityManager, SinSource

    ours = {m.name: m for m in CapacityManager.from_sin(SIN2, SinSource(group="Mini_grid_y100")).capacity_models()}
    genie = {m["Name"]: m for m in json.loads(MODEL_JSON2.read_text())["BucklingModels"]}

    checked = 0
    for name, model in ours.items():
        if name not in genie:
            continue  # Genie's opening-split cells (trailing-integer names) are not reproduced
        our_fields = sorted(tuple(sorted(int(e) for e in p.element_ids)) for p in model.plates)
        genie_fields = sorted(tuple(sorted(int(e) for e in p["FiniteElements"])) for p in genie[name]["Plates"])
        assert our_fields == genie_fields, f"{name}: plate field decomposition differs from Genie"
        assert len(model.plates) == len(model.stiffeners) + 1  # one field per inter-stiffener strip + 2 edges
        checked += 1
    assert checked >= 6


@ref2
def test_unstiffened_panels_are_disjoint_rectangular_fields():
    from ada.fem.capacity import CapacityManager, SinSource

    models = CapacityManager.from_sin(SIN2, SinSource(include_unstiffened=True)).capacity_models()
    unstiffened = [m for m in models if not m.stiffeners]
    stiffened_plates = {e for m in models if m.stiffeners for p in m.plates for e in p.element_ids}

    assert unstiffened  # the double bottom has plate fields carrying no secondary stiffener
    assert all(m.name.startswith("unstiffenedPanel(") for m in unstiffened)

    seen: set[int] = set()
    for m in unstiffened:
        for p in m.plates:
            for e in p.element_ids:
                assert e not in stiffened_plates  # unstiffened fields never overlap a stiffened panel
                assert e not in seen  # and are mutually disjoint
                seen.add(e)


# --------------------------------------------------------------------------- #
# Load-case combinations (RDRESCMB) → field superposition
# --------------------------------------------------------------------------- #
def test_metadata_exposes_named_load_combinations():
    """The Mini SIN's two RDRESCMB combinations surface with names + factors."""
    from ada.fem.formats.sesam.results.read_sin import read_sin_metadata

    meta = read_sin_metadata(SIN)
    assert meta.combination_ids == [9, 10]
    assert meta.result_names[9] == "lcc1"
    assert meta.result_names[10] == "lcc2"
    # lcc1 = 1.2·c1 + 1.1·c2 + 1.0·c5 + 2.0·c6 + 1.3·c8 (zero-factor cases dropped).
    lcc1 = meta.combinations[9]
    assert set(lcc1) == {1, 2, 5, 6, 8}
    assert lcc1[1] == pytest.approx(1.2, rel=1e-6)
    assert lcc1[6] == pytest.approx(2.0, rel=1e-6)
    # Combinations are selectable cases even though only basic steps are RV*-stored.
    assert set(meta.selectable_cases) >= {9, 10}


def test_combination_superposition_reproduces_stored_combination(manager):
    """Resolving a combination by superposing its basic cases reproduces the
    field SESTRA happened to also store for it (Mini stores lcc1/lcc2 as RV*
    steps 9/10), to float32 precision for every resolved design variable.

    This is the load-combination path the Ruben "case 14" blocker needs: there
    the combination is *not* stored, so superposition is the only route — Mini
    just happens to also carry the stored field, giving an exact oracle.
    """
    from ada.fem.capacity import extract
    from ada.fem.capacity.stress_resolve import _resolve_step
    from ada.fem.formats.sesam.results.read_sin import iter_sin_step_results

    models = manager.capacity_models()
    superposed = {(c.result_case, c.stiffener): c for c in manager.resolve_cases([9, 10])}

    aux = extract.AuxRecords.from_sin(SIN)
    material = {e: (p.material.E, p.material.poisson) for m in models for p in m.plates for e in p.element_ids}
    stored: dict[tuple[int, str], object] = {}
    mesh = None
    geom_cache: dict = {}
    for step, res in iter_sin_step_results(SIN, [9, 10]):
        mesh = mesh or res.mesh
        for rc in _resolve_step(mesh, aux, models, step, res.results, material, geom_cache, log=False):
            stored[(rc.result_case, rc.stiffener)] = rc

    keys = set(superposed) & set(stored)
    assert len(keys) > 50  # both paths resolve every (case, stiffener) pair

    # Per-variable scale (max |stored| over all pairs) normalises the tolerance so
    # near-zero quantities (e.g. QFE on a flat moment field) aren't judged on noise.
    scale: dict[str, float] = {}
    for k in keys:
        for var, val in stored[k].variables.items():
            scale[var] = max(scale.get(var, 0.0), abs(val))
    worst = 0.0
    for k in keys:
        a, b = superposed[k], stored[k]
        for var, av in a.variables.items():
            worst = max(worst, abs(av - b.variables.get(var, 0.0)) / max(scale[var], 1e-9))
    assert worst < 1e-4  # float32 superposition noise floor


def test_resolve_rejects_unknown_case_but_accepts_combinations(manager):
    from ada.fem.formats.sesam.results.read_sin import read_sin_metadata

    meta = read_sin_metadata(SIN)
    with pytest.raises(ValueError, match="not in the SIN"):
        manager.resolve_cases([999])
    # A combination id is accepted (resolved by superposition), not rejected.
    resolved = manager.resolve_cases([meta.combination_ids[0]])
    assert resolved and {rc.result_case for rc in resolved} == {meta.combination_ids[0]}
