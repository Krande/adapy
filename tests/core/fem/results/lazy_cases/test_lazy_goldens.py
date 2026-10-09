"""Every combination materialised lazily equals the eager bake of it.

The eager bake (``lazy_combinations=False``) superposes each combination from the
solver's raw records before deriving anything -- what every bake did before lazy
cases. The lazy base bake holds the stored cases only; each combination is then
materialised by Tier A (superposing the baked strides) or, when its recipe needs
the raw records, from the source.

Tolerance table (Tier A vs eager), per field:

* ``EXACT`` fields -- the components the bake writes as the solver's raw words,
  and the derivations made from them (von Mises, principal stresses at result
  points / element nodes, the displacement magnitude): value-identical, NaN
  exactly where the eager bake has NaN, ``+0 == -0``.
* every other field (averages, membrane/bending splits, stress resultants, beam
  stresses -- linear, but formed in double and rounded by the bake before they
  are superposed here -- and what is derived from them):
  ``|lazy - eager| <= K * eps32 * S`` with ``S = sum_k |c_k| * max |x_k|``, the
  largest magnitude of the blob (every entity and component) in each stored
  case ``x_k`` weighted by its coefficient. The bound is per blob, not per
  entity: a stress resultant's rounding comes from the surface stresses it was
  formed from, which can be large where the resultant itself is small (pure
  bending), so only the blob's scale bounds it. Observed K <= 2.6 (shell stress
  resultants; averages and their derivations <= 2.1) on these decks and on a
  mid-size synthetic deck of 7.7k shells and 90 combinations; K = 8 is the
  test's margin.

The raw path is value-identical to the eager bake for every field.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from ada.fem.results.artefacts import read_blob_header, read_elem_field_blob_header
from ada.fem.results.artefacts.combine import (
    CASE_OVERLAY_NAME,
    _buckets,
    case_dir_name,
    local_stride_fetcher,
    materialise_case,
    materialise_case_from_source,
    superpose_case,
)

EPS32 = float(np.finfo(np.float32).eps)

#: Fields whose every component is value-identical to the eager bake.
EXACT = {
    "sesam.nodes.displacement",
    "REACTION-FORCE",
    "RVNODDIS",
    "STRESS",
    "FORCES",
    "sesam.resultpoints.g_stress",
    "sesam.elements.g_stress",
    "sesam.resultpoints.p_stress",
    "sesam.elements.p_stress",
    "sesam.resultpoints.g_force",
    "sesam.elements.g_force",
}

#: K of the tolerance above, for every field not in EXACT.
K = 8.0


def _eager_stride(baked, name, elem_type, n):
    field = next(f for f in baked.eager["fields"] if f["name_canonical"] == name)
    step = next(s["i"] for s in field["steps"] if s["value"] == n)
    bucket = next(b for b in _buckets(baked.eager, field) if b.elem_type == elem_type)
    raw = local_stride_fetcher(baked.eager_dir)(bucket.blob, step)
    return np.frombuffer(raw, dtype="<f4").reshape(bucket.shape)


def _magnitude(baked, entry, name, elem_type) -> float:
    """``S`` of the tolerance: sum_k |c_k| * max |x_k| over the blob (entities and components)."""
    field = next(f for f in baked.base["fields"] if f["name_canonical"] == name)
    bucket = next(b for b in _buckets(baked.base, field) if b.elem_type == elem_type)
    fetch = local_stride_fetcher(baked.base_dir)
    total = 0.0
    for (basic, _, _), (c, _) in zip(entry["terms"], entry["coefficients"]):
        step = next(s["i"] for s in field["steps"] if s["value"] == basic)
        x = np.abs(np.frombuffer(fetch(bucket.blob, step), dtype="<f4").astype(np.float64))
        total += abs(c) * float(np.nanmax(x)) if np.isfinite(x).any() else 0.0
    return total


def _assert_value_identical(got, want, what):
    assert got.shape == want.shape, what
    assert np.array_equal(np.isnan(got), np.isnan(want)), f"{what}: NaN layout differs"
    assert np.array_equal(got, want, equal_nan=True), f"{what}: not value-identical"


def test_the_base_bake_holds_the_stored_cases_only(baked_deck):
    deck = baked_deck.deck
    stored = sorted(deck.basics + deck.complex_basics)
    base = baked_deck.base
    assert base["bake_version"] == 4
    assert base["baked_steps"] == stored
    assert base["baked_steps_hint"]
    assert sorted(c["n"] for c in base["combination_steps"]) == sorted(deck.combinations)
    assert base["lazy_cases"] == {"version": 1, "server": True, "client_tier_a": True, "cases_prefix": "cases/"}
    for field in base["fields"]:
        if field.get("category") == "property":
            continue
        assert [s["value"] for s in field["steps"]] == [float(s) for s in stored], field["name_canonical"]
    # The eager bake keeps every case, and says nothing about lazy ones.
    assert "combination_steps" not in baked_deck.eager
    assert "baked_steps" not in baked_deck.eager


def test_needs_raw_is_exactly_a_complex_case_at_a_non_zero_phase(baked_deck):
    complex_basics = set(baked_deck.deck.complex_basics)
    for entry in baked_deck.base["combination_steps"]:
        cx, terms = baked_deck.deck.combinations[entry["n"]]
        expected = any(b in complex_basics and np.float32(f * np.sin(p)) != 0 for b, f, p in terms)
        assert entry["needs_raw"] is expected, entry
        assert entry["complex"] is cx
        assert [tuple(t) for t in entry["terms"]] == [tuple(t) for t in terms]


def test_every_tier_a_combination_matches_the_eager_bake(baked_deck):
    fetch = local_stride_fetcher(baked_deck.base_dir)
    checked = 0
    for entry in baked_deck.base["combination_steps"]:
        if entry["needs_raw"]:
            continue
        n = entry["n"]
        for (name, elem_type), got in superpose_case(baked_deck.base, fetch, n).arrays.items():
            want = _eager_stride(baked_deck, name, elem_type, n)
            what = f"case {n} {name} {elem_type}"
            if name in EXACT:
                _assert_value_identical(got, want, what)
            else:
                assert np.array_equal(np.isnan(got), np.isnan(want)), f"{what}: NaN layout differs"
                bound = K * EPS32 * _magnitude(baked_deck, entry, name, elem_type)
                diff = np.abs(got.astype(np.float64) - want)
                ok = np.isnan(want) | (diff <= bound)
                assert ok.all(), f"{what}: {float(np.nanmax(diff) / max(bound, 1e-300) * K):.2f} eps*S"
            checked += 1
    assert checked


def test_every_raw_combination_matches_the_eager_bake(baked_deck, tmp_path):
    raw = [e for e in baked_deck.base["combination_steps"] if e["needs_raw"]]
    assert raw, "the deck must exercise the raw path"
    for entry in raw:
        out = tmp_path / case_dir_name(entry)
        overlay = materialise_case_from_source(baked_deck.deck.path, baked_deck.base, entry["n"], out)
        assert overlay["producer"]["tier"] == "raw"
        assert overlay["case"]["recipe_hash"] == entry["recipe_hash"]
        assert (out / CASE_OVERLAY_NAME).exists()
        for field in overlay["fields"]:
            name = field["name_canonical"]
            buckets = field.get("per_type") or [{"elem_type": None, "blob": field["blob"]}]
            for b in buckets:
                want = _eager_stride(baked_deck, name, b["elem_type"], entry["n"])
                got = np.fromfile(out / b["blob"]["url"], dtype="<f4", offset=b["blob"]["header_bytes"])
                _assert_value_identical(got.reshape(want.shape), want, f"raw case {entry['n']} {name}")


def test_a_written_case_has_the_bakes_single_step_layout(baked_deck, tmp_path):
    """Tier A writes blobs a single-case bake would: same names, same header bytes,
    and the per-step scalar range of what it wrote."""
    entry = next(e for e in baked_deck.base["combination_steps"] if not e["needs_raw"])
    lazy_dir = tmp_path / "lazy"
    overlay = materialise_case(baked_deck.base, local_stride_fetcher(baked_deck.base_dir), entry["n"], out_dir=lazy_dir)
    raw_dir = tmp_path / "raw"
    raw = materialise_case_from_source(baked_deck.deck.path, baked_deck.base, entry["n"], raw_dir)
    assert json.loads((lazy_dir / CASE_OVERLAY_NAME).read_text()) == overlay
    assert overlay["producer"]["tier"] == "A" and overlay["producer"]["engine"] == "numpy"
    assert [f["name_canonical"] for f in overlay["fields"]] == [f["name_canonical"] for f in raw["fields"]]
    for lf, rf in zip(overlay["fields"], raw["fields"]):
        assert lf["steps"] == rf["steps"]
        lb = lf.get("per_type") or [lf]
        rb = rf.get("per_type") or [rf]
        for a, b in zip(lb, rb):
            assert a["blob"] == b["blob"]
            head = read_elem_field_blob_header if lf.get("per_type") else read_blob_header
            assert head(lazy_dir / a["blob"]["url"]) == head(raw_dir / b["blob"]["url"])
            assert head(lazy_dir / a["blob"]["url"])["n_steps"] == 1
            assert (lazy_dir / a["blob"]["url"]).read_bytes()[:1024] == (raw_dir / b["blob"]["url"]).read_bytes()[:1024]
        if lf["name_canonical"] in EXACT:
            assert lf["scalar_range"] == rf["scalar_range"]


def test_the_synthetic_deck_averages_eight_shells_at_a_node(synthetic_deck):
    """The deck exercises the >= 8 contributor branch of the shell nodal average."""
    from ada.fem.formats.sesam.results.sin_reader import open_sin

    counts: dict[int, int] = {}
    with open_sin(str(synthetic_deck.deck.path)) as sin:
        for rec in sin.iter_records("GELMNT1"):
            if int(rec[2]) in (24, 25):
                for node in rec[4:]:
                    counts[int(node)] = counts.get(int(node), 0) + 1
    eight = [n for n, c in counts.items() if c >= 8]
    assert eight
    field = next(f for f in synthetic_deck.eager["fields"] if f["name_canonical"] == "sesam.nodes.g_stress")
    labels = synthetic_deck.eager["mesh"]["node_labels"]
    values = _eager_stride(synthetic_deck, field["name_canonical"], None, synthetic_deck.deck.basics[0] + 100)
    finite = [n for n in eight if np.isfinite(values[labels.index(n)]).all()]
    assert finite, "a node with eight contributors must carry an average"


@pytest.mark.parametrize("which", ["fixture", "synthetic"])
def test_the_eager_bake_is_unchanged_by_the_lazy_keys(request, which):
    """Apart from bake_version and the additive per-field keys, the eager manifest is
    what the bake wrote before lazy cases existed: steps for every case."""
    baked = request.getfixturevalue(f"{which}_deck")
    stored = len(baked.deck.basics) + len(baked.deck.complex_basics)
    n_steps = {f["n_steps"] for f in baked.eager["fields"] if f.get("category") != "property"}
    assert n_steps == {stored + len(baked.deck.combinations)}
