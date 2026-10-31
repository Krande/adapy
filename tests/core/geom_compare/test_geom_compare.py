"""ada.geom_compare: two sources' tessellations of the same elements, compared."""

from __future__ import annotations

import pathlib

import pytest

from ada.geom_compare import (
    FAIL,
    MISSING,
    PASS,
    UNCERTAIN,
    GlbSource,
    Thresholds,
    classify,
    compare_sources,
    glb,
    judge,
    report,
)
from ada.geom_compare.distance import surface_distance
from ada.geom_compare.measure import Measure, mesh_area, mesh_volume_bbox
from ada.geom_compare.summary import summarise_glb

# A closed unit cube (12 outward triangles), in metres.
_CUBE_V = [0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0, 0, 0, 1, 1, 0, 1, 1, 1, 1, 0, 1, 1]
_CUBE_T = [0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7, 0, 1, 5, 0, 5, 4, 1, 2, 6, 1, 6, 5, 2, 3, 7, 2, 7, 6, 3, 0, 4, 3, 4, 7]


def _cube(size=1.0, at=(0.0, 0.0, 0.0)):
    return [at[i % 3] + v * size for i, v in enumerate(_CUBE_V)], list(_CUBE_T)


def _glb_bytes(tmp_path: pathlib.Path, meshes: dict, name="m.glb") -> bytes:
    path = tmp_path / name
    glb.write(meshes, path)
    return path.read_bytes()


# --- glb -------------------------------------------------------------------------------------------


def test_a_written_glb_reads_back_per_element(tmp_path):
    glb.write({"/A": _cube(), "B": _cube(2.0, (5, 0, 0))}, tmp_path / "x.glb")
    els = glb.load(tmp_path / "x.glb")
    assert set(els) == {"/A", "B"}
    assert els["/A"].triangles == 12 and els["/A"].volume == pytest.approx(1.0)
    assert els["B"].bounds.min == pytest.approx((5.0, 0.0, 0.0)) and els["B"].volume == pytest.approx(8.0)


def test_names_join_case_and_slash_blind_and_repeats_fold():
    a = glb.Element("/x", glb.Bounds((0, 0, 0), (1, 1, 1)), 2, 1.0)
    b = glb.Element("X", glb.Bounds((1, 1, 1), (2, 2, 2)), 3, 2.0)
    folded = glb.index_by_name([a, b])
    assert list(folded) == ["X"] and folded["X"].triangles == 5 and folded["X"].bounds.max == (2, 2, 2)


def test_element_meshes_and_closure(tmp_path):
    js, binary = glb.parse_glb(_glb_bytes(tmp_path, {"A": _cube(), "B": _cube(1.0, (3, 0, 0))}))
    h = glb.merged_hierarchy(js)
    got = glb.element_meshes(js, binary, h, ["B"])
    assert set(got) == {"B"} and len(got["B"].indices) == 36 and len(got["B"].positions) == 24
    assert glb.closure(got["B"]) is True
    opened = glb.ElementMesh("B", got["B"].positions, got["B"].indices[:-3])
    assert glb.closure(opened) is False


def test_a_glb_without_the_hierarchy_has_nothing_to_compare(tmp_path):
    data = _glb_bytes(tmp_path, {"A": _cube()})
    js, binary = glb.parse_glb(data)
    js["scenes"][0].pop("extras")
    glb.write_glb(js, binary, tmp_path / "bare.glb")
    assert glb.load(tmp_path / "bare.glb") == {}


# --- summary --------------------------------------------------------------------------------------------


def test_summaries_match_the_exact_reader_in_mm_and_z_up(tmp_path):
    data = _glb_bytes(tmp_path, {"A": _cube(0.5, (1, 2, 3))})
    s = summarise_glb(data, z_up=False)["A"]
    assert s.volume == pytest.approx(0.125e9) and s.area == pytest.approx(1.5e6)
    assert s.bbox_min == pytest.approx((1000, 2000, 3000)) and s.triangles == 12
    up = summarise_glb(data)["A"]  # Y-up GLB read as Z-up: (x, -z, y)
    assert up.bbox_min == pytest.approx((1000, -3500, 2000)) and up.volume == pytest.approx(0.125e9)


def test_the_fingerprint_follows_the_geometry(tmp_path):
    one = summarise_glb(_glb_bytes(tmp_path, {"A": _cube()}, "1.glb"))["A"].fingerprint
    same = summarise_glb(_glb_bytes(tmp_path, {"A": _cube()}, "2.glb"))["A"].fingerprint
    moved = summarise_glb(_glb_bytes(tmp_path, {"A": _cube(1.0, (0.001, 0, 0))}, "3.glb"))["A"].fingerprint
    assert one == same != moved


def test_names_override_per_glb_id(tmp_path):
    data = _glb_bytes(tmp_path, {"entry": _cube()})
    assert list(summarise_glb(data, names={"1": "/SITE/ELEMENT"})) == ["SITE/ELEMENT"]


# --- measure / judge --------------------------------------------------------------------------------


def test_mesh_volume_and_area_of_a_cube():
    pts = [tuple(_CUBE_V[i : i + 3]) for i in range(0, 24, 3)]
    tris = [_CUBE_T[i : i + 3] for i in range(0, 36, 3)]
    vol, (lo, hi) = mesh_volume_bbox(pts, tris)
    assert vol == pytest.approx(1.0) and lo == (0, 0, 0) and hi == (1, 1, 1)
    assert mesh_area(pts, tris) == pytest.approx(6.0)


def test_judge_names_the_other_source_it_was_told():
    m = Measure((0, 0, 0), (10, 10, 10), 1000.0)
    assert judge("x", m, None, theirs_label="the reference").reason == "the reference has no element of this name"
    assert judge("x", m, m).passed
    off = Measure((0, 0, 0), (10, 10, 13), 1300.0)
    v = judge("x", m, off)
    assert not v.passed and "box off by 3.0 mm" in v.reason and "volume" in v.reason


# --- distance / classify --------------------------------------------------------------------------------


def test_surface_distance_is_zero_for_the_same_mesh_and_the_offset_for_a_moved_one():
    a = _cube(1000.0)
    assert surface_distance(*a, *a, samples=600).hausdorff == pytest.approx(0.0, abs=1e-9)
    moved = _cube(1000.0, (30.0, 0, 0))
    d = surface_distance(*a, *moved, samples=2000)
    assert 20.0 < d.hausdorff <= 30.0 + 1e-6 and d.spacing > 0


def _summary(tmp_path, mesh, name="E", file="s.glb"):
    return summarise_glb(_glb_bytes(tmp_path, {name: mesh}, file), z_up=False)[name.upper()]


def test_the_classification_paths(tmp_path):
    t = Thresholds()
    a = _summary(tmp_path, _cube(), file="a.glb")
    assert classify(a, a, t).verdict == PASS
    assert classify(a, None, t).verdict == MISSING and classify(None, a, t).metrics["missing"] == "first"
    far = _summary(tmp_path, _cube(1.0, (0.1, 0, 0)), file="far.glb")  # 100 mm
    assert classify(a, far, t).verdict == FAIL
    near = _summary(tmp_path, _cube(1.0, (0.005, 0, 0)), file="near.glb")  # 5 mm: undecided by the box
    assert classify(a, near, t).verdict == UNCERTAIN  # no surfaces asked for

    class D:
        hausdorff, p95, mean, spacing = 1.5, 1.0, 0.5, 0.2

    res = classify(a, near, t, lambda: D())
    assert res.verdict == PASS and res.metrics["hausdorff_mm"] == 1.5
    D.hausdorff = 5.0
    assert classify(a, near, t, lambda: D()).verdict == UNCERTAIN


def test_thresholds_from_settings_keep_known_positive_numbers():
    t = Thresholds.from_options({"fail_mm": "10", "box_tol_mm": -1, "other": 3})
    assert t.fail_mm == 10.0 and t.box_tol_mm == 1.0


# --- compare_sources ------------------------------------------------------------------------------------


def _src(tmp_path, ref, meshes, **kw):
    data = _glb_bytes(tmp_path, meshes, f"{ref}.glb")
    return GlbSource(ref=ref, load=lambda: data, **kw)


def test_two_sources_compared_with_missing_and_a_moved_element(tmp_path):
    a = [_src(tmp_path, "a", {"/S/ONE": _cube(), "/S/TWO": _cube(1.0, (5, 0, 0)), "/S/ONLY_A": _cube()})]
    b = [_src(tmp_path, "b", {"S/ONE": _cube(), "S/TWO": _cube(1.0, (5.2, 0, 0)), "S/ONLY_B": _cube()})]
    rendered = []

    def render(ma, mb):
        rendered.append((ma is not None, mb is not None))
        return b"png-a" if ma else None, b"png-b" if mb else None, None

    res = compare_sources(a, b, render=render)
    by = {r["key"]: r for r in res.rows}
    assert by["S/ONE"]["verdict"] == PASS and by["S/TWO"]["verdict"] == FAIL
    assert by["S/ONLY_A"]["verdict"] == MISSING and by["S/ONLY_B"]["verdict"] == MISSING
    assert res.counts["compared"] == 2 and res.counts[MISSING] == 2
    # Pictures for what did not pass only.
    assert "_png_a" in by["S/TWO"] and "_png_a" not in by["S/ONE"]
    assert len(rendered) == 3 and not res.warnings


def test_an_unchanged_pair_is_reused_not_measured(tmp_path):
    a = [_src(tmp_path, "a", {"X": _cube()})]
    b = [_src(tmp_path, "b", {"X": _cube(1.0, (0.005, 0, 0))})]
    first = compare_sources(a, b, render=None)
    row = first.rows[0]
    previous = {row["key"]: {**row, "verdict": FAIL, "reason": "kept"}}
    second = compare_sources(a, b, previous=previous)
    assert second.rows[0]["verdict"] == FAIL and second.rows[0]["reason"] == "kept" and second.counts["reused"] == 1


def test_no_name_on_both_sides_is_a_broken_join(tmp_path):
    res = compare_sources([_src(tmp_path, "a", {"P": _cube()})], [_src(tmp_path, "b", {"Q": _cube()})])
    assert any("broken join" in w for w in res.warnings)


def test_the_surfaces_decide_a_pair_the_box_cannot(tmp_path):
    a = [_src(tmp_path, "a", {"X": _cube()})]
    b = [_src(tmp_path, "b", {"X": _cube(1.0, (0.005, 0, 0))})]  # 5 mm: between box tol and fail
    res = compare_sources(a, b)
    assert res.rows[0]["verdict"] == UNCERTAIN and res.counts["measured_surfaces"] == 1
    assert 3.0 < res.rows[0]["metrics"]["hausdorff_mm"] <= 5.0 + 1e-6


# --- report ---------------------------------------------------------------------------------------------


def test_the_two_file_report_and_its_summary(tmp_path):
    glb.write({"/A": _cube(), "/B": _cube(1.0, (5, 0, 0))}, tmp_path / "ours.glb")
    glb.write({"A": _cube(), "B": _cube(1.0, (5.01, 0, 0)), "C": _cube()}, tmp_path / "theirs.glb")
    r = report.compare_files(tmp_path / "ours.glb", tmp_path / "theirs.glb", root="/R", theirs_is_y_up=False)
    assert r["compared"] == 2 and r["differing"] == 1 and r["only_theirs"] == ["C"]
    assert r["differences"][0]["worst_mm"] == pytest.approx(10.0, abs=1e-3)
    assert "1 of 2 elements differ" in report.summarise(r)
    assert "broken join" in report.summarise({"compared": 0})
