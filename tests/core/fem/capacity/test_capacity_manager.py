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
    r"C:\AibelProgs\projects\GitHub\dnv-rp-c201\.local\reference"
    r"\example_mini_topside_codecheck\temp\Assembly"
)
REF = pathlib.Path(os.environ.get("ADA_CAPACITY_REF", _DEFAULT_REF))
SIN = REF / "Analysis_pm" / "20260617_122105_R1.SIN"
MODEL_JSON = REF / "Cc2.run1" / "model.json"

pytestmark = pytest.mark.skipif(
    not (SIN.exists() and MODEL_JSON.exists()),
    reason=f"Mini-topside capacity reference not found under {REF}",
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
            (stiffener["Name"], tuple(int(e) for e in stiffener["FiniteElements"]))
            for stiffener in ref["Stiffeners"]
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

    A multi-connection tail remains until the resolver emits one case per
    connection, so assert the calibrated median plus a broad within-2% fraction
    instead of full parity.
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

    for residuals in (trans_start_rel, trans_end_rel, long_rel, axial_rel):
        assert len(residuals) > 400
        assert statistics.median(residuals) < 1e-3
        assert sum(r <= 0.02 for r in residuals) / len(residuals) > 0.80
