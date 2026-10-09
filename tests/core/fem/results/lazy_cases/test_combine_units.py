"""The pieces of artefacts/combine.py, pinned exactly: other engines (a native or
WASM kernel) are compared against this reference, so its arithmetic, op formulas
and recipe hash are part of the contract."""

from __future__ import annotations

import math

import numpy as np
import pytest

from ada.fem.formats.sesam.results import derived_values as dv
from ada.fem.results.artefacts.combine import (
    DERIVATION_OPS,
    NeedsRawRecords,
    classify_fields,
    combination_entry,
    derivation_ops_table,
    envelope,
    local_stride_fetcher,
    materialise_case,
    recipe_hash,
    superpose,
    superpose_case,
    term_coefficients,
)


def f32(x: float) -> float:
    return float(np.float32(x))


# ── superposition ───────────────────────────────────────────────────────────


def test_superpose_is_float32_in_term_order_with_two_roundings_per_term():
    x = np.array([16777216.0], np.float32)  # 2**24: the float32 spacing there is 2
    y = np.array([1.0], np.float32)
    # x + 1 + 1 rounds back to x twice; 1 + 1 + x does not -- so the order is load-bearing.
    assert superpose([x, y, y], [1.0, 1.0, 1.0])[0] == np.float32(16777216.0)
    assert superpose([y, y, x], [1.0, 1.0, 1.0])[0] == np.float32(16777218.0)
    rng = np.random.default_rng(0)
    xs = [rng.standard_normal(64).astype(np.float32) * 1e7 for _ in range(5)]
    cs = [f32(c) for c in (1.2, -0.9, 1.35, 0.7, -1.1)]
    want = xs[0] * np.float32(cs[0])
    for x_, c in zip(xs[1:], cs[1:]):
        tmp = x_ * np.float32(c)  # rounded on its own: no fused multiply-add
        want = want + tmp
    got = superpose(xs, cs)
    assert got.dtype == np.float32
    assert np.array_equal(got, want)
    # Associating differently is NOT the same thing.
    assert not np.array_equal(got, xs[0] * np.float32(cs[0]) + sum(x_ * np.float32(c) for x_, c in zip(xs[1:], cs[1:])))


def test_coefficients_round_once_from_double_and_are_exact_at_zero_phase():
    assert term_coefficients(f32(1.2), 0.0) == (f32(1.2), 0.0)
    c, s = term_coefficients(f32(12.5), f32(0.5236))
    assert c == f32(f32(12.5) * math.cos(f32(0.5236)))
    assert s == f32(f32(12.5) * math.sin(f32(0.5236)))


# ── derivation ops: the bake's own formulas ─────────────────────────────────


@pytest.fixture
def stresses():
    rng = np.random.default_rng(1)
    return [(rng.standard_normal(500) * 1e8).astype(np.float32) for _ in range(3)]


def test_ops_reproduce_the_bake_bit_for_bit(stresses):
    sx, sy, txy = stresses
    assert np.array_equal(
        DERIVATION_OPS["plane_von_mises"]["impl"](sx, sy, txy), dv.plane_von_mises(sx, sy, txy).astype(np.float32)
    )
    p = dv.plane_principal(sx, sy, txy).astype(np.float32)
    assert np.array_equal(DERIVATION_OPS["plane_principal_1"]["impl"](sx, sy, txy), p[..., 0])
    assert np.array_equal(DERIVATION_OPS["plane_principal_2"]["impl"](sx, sy, txy), p[..., 1])
    xyz = np.stack(stresses, axis=-1)
    norm = np.linalg.norm(xyz.astype(float), axis=1).astype(np.float32)
    assert np.array_equal(DERIVATION_OPS["magnitude3"]["impl"](sx, sy, txy), norm)


def test_the_op_table_is_machine_readable():
    table = derivation_ops_table()
    assert set(table) == {"magnitude3", "plane_von_mises", "plane_principal_1", "plane_principal_2"}
    for spec in table.values():
        assert set(spec) == {"inputs", "formula", "reference"} and spec["inputs"] == 3


# ── recipes ─────────────────────────────────────────────────────────────────


def test_recipe_hash_is_pinned():
    """Pinned values: an engine that hashes recipes must produce these."""
    assert (
        recipe_hash(101, False, [(1, f32(1.2), 0.0), (3, f32(1.1), 0.0)])
        == "2b1b9588124c8a560181aaf1ef7e1d81a45b049a6a0f6e4b91bba4ba4b0686ba"
    )
    assert (
        recipe_hash(22, True, [(60, 1.0, 0.0), (60, 1.0, f32(1.0472))])
        == "b76c14950ec0802c482a730563572a7865e7cd963620d67083ebd13027f9689e"
    )
    # Term order, the complex flag and the phase are all part of the recipe.
    base = recipe_hash(101, False, [(1, f32(1.2), 0.0), (3, f32(1.1), 0.0)])
    assert recipe_hash(101, False, [(3, f32(1.1), 0.0), (1, f32(1.2), 0.0)]) != base
    assert recipe_hash(101, True, [(1, f32(1.2), 0.0), (3, f32(1.1), 0.0)]) != base
    assert recipe_hash(101, False, [(1, f32(1.2), f32(0.1)), (3, f32(1.1), 0.0)]) != base


def test_combination_entry_flags_what_tier_a_cannot_do():
    real = combination_entry(
        101, False, [(1, f32(1.2), 0.0), (2, f32(0.5), f32(0.5236))], complex_basics=[], baked=[1, 2]
    )
    assert real["needs_raw"] is False and "raw_reason" not in real
    assert real["coefficients"][1] == list(term_coefficients(f32(0.5), f32(0.5236)))
    at_zero = combination_entry(102, False, [(1, 1.0, 0.0), (9, 12.5, 0.0)], complex_basics=[9], baked=[1, 9])
    assert at_zero["needs_raw"] is False
    phased = combination_entry(103, False, [(1, 1.0, 0.0), (9, 12.5, f32(0.5236))], complex_basics=[9], baked=[1, 9])
    assert phased["needs_raw"] is True and "complex basic case 9" in phased["raw_reason"]
    unbaked = combination_entry(104, False, [(1, 1.0, 0.0), (5, 1.0, 0.0)], complex_basics=[], baked=[1])
    assert unbaked["needs_raw"] is True and "not baked" in unbaked["raw_reason"]
    assert real["recipe_hash"] == recipe_hash(101, False, real["terms"])


# ── field classes ───────────────────────────────────────────────────────────


def _field(name, comps, position, attribute, surface="", **kw):
    return {"name_canonical": name, "components": comps, "group_path": [position, attribute], "surface": surface, **kw}


def test_classify_fields():
    fields = [
        _field("d", ["ALL", "X", "Y", "Z", "RX", "RY", "RZ"], "Nodes", "DISPLACEMENT"),
        _field("g", ["SIGXX", "SIGYY", "TAUXY", "VONMISES"], "Nodes", "G-STRESS", "upper"),
        _field("gl", ["SIGXX", "SIGYY", "TAUXY", "VONMISES"], "Nodes", "G-STRESS", "lower"),
        _field("pl", ["P1", "P2"], "Nodes", "P-STRESS", "lower"),
        _field("pm", ["P1", "P2"], "Nodes", "PM-STRESS"),  # no D-STRESS: not combinable
        _field("r", ["NXX", "MXX"], "Nodes", "R-STRESS"),
        {"name_canonical": "props.thickness", "components": ["t"], "category": "property"},
        {"name_canonical": "STRESS", "components": ["a", "b"]},
        {"name_canonical": "whatever", "components": ["a"]},
    ]
    got = classify_fields(fields)
    assert got["d"] == {
        "linear_components": ["X", "Y", "Z", "RX", "RY", "RZ"],
        "derived_components": {"ALL": {"op": "magnitude3", "args": ["X", "Y", "Z"]}},
    }
    assert got["g"]["derived_components"] == {
        "VONMISES": {"op": "plane_von_mises", "args": ["SIGXX", "SIGYY", "TAUXY"]}
    }
    assert got["pl"] == {
        "linear_components": [],
        "derived_components": {
            "P1": {"op": "plane_principal_1", "args": ["SIGXX", "SIGYY", "TAUXY"], "field": "gl"},
            "P2": {"op": "plane_principal_2", "args": ["SIGXX", "SIGYY", "TAUXY"], "field": "gl"},
        },
    }
    assert got["r"] == {"linear_components": ["NXX", "MXX"], "derived_components": {}}
    assert got["STRESS"]["linear_components"] == ["a", "b"]
    assert {"pm", "props.thickness", "whatever"}.isdisjoint(got)


def test_every_field_of_a_bake_is_classified(synthetic_deck):
    per_case = [f for f in synthetic_deck.base["fields"] if f.get("category") != "property"]
    assert per_case and all("linear_components" in f for f in per_case)
    p = next(f for f in per_case if f["name_canonical"] == "sesam.resultpoints.p_stress")
    assert p["derived_components"]["P1"]["field"] == "sesam.resultpoints.g_stress"


# ── materialising ───────────────────────────────────────────────────────────


def test_a_raw_recipe_is_refused_by_tier_a(synthetic_deck):
    entry = next(e for e in synthetic_deck.base["combination_steps"] if e["needs_raw"])
    with pytest.raises(NeedsRawRecords, match="non-zero phase"):
        materialise_case(synthetic_deck.base, local_stride_fetcher(synthetic_deck.base_dir), entry["n"])


def test_a_manifest_without_field_classes_still_combines(synthetic_deck):
    """A base bake's own keys win; without them (an older writer) the classes are inferred."""
    stripped = {
        **synthetic_deck.base,
        "fields": [
            {k: v for k, v in f.items() if k not in ("linear_components", "derived_components")}
            for f in synthetic_deck.base["fields"]
        ],
    }
    entry = next(e for e in synthetic_deck.base["combination_steps"] if not e["needs_raw"])
    fetch = local_stride_fetcher(synthetic_deck.base_dir)
    a = superpose_case(synthetic_deck.base, fetch, entry["n"]).arrays
    b = superpose_case(stripped, fetch, entry["n"]).arrays
    assert a.keys() == b.keys()
    assert all(np.array_equal(a[k], b[k], equal_nan=True) for k in a)


def test_a_subset_of_fields_pulls_in_its_derivation_sources(synthetic_deck):
    entry = next(e for e in synthetic_deck.base["combination_steps"] if not e["needs_raw"])
    fetch = local_stride_fetcher(synthetic_deck.base_dir)
    only = superpose_case(synthetic_deck.base, fetch, entry["n"], ["sesam.resultpoints.p_stress"])
    full = superpose_case(synthetic_deck.base, fetch, entry["n"])
    assert {k[0] for k in only.arrays} == {"sesam.resultpoints.p_stress"}
    for k, v in only.arrays.items():
        assert np.array_equal(v, full.arrays[k])


def test_envelope_is_the_max_and_min_over_the_cases(synthetic_deck):
    base, fetch = synthetic_deck.base, local_stride_fetcher(synthetic_deck.base_dir)
    field = "sesam.elements.g_stress"
    env = envelope(base, fetch, field)
    cases = [e["n"] for e in base["combination_steps"] if not e["needs_raw"]]
    assert env.cases == cases
    for key in env.maximum:
        stack = np.stack([superpose_case(base, fetch, n, [field]).arrays[key] for n in cases])
        assert np.array_equal(env.maximum[key], stack.max(axis=0))
        assert np.array_equal(env.minimum[key], stack.min(axis=0))
        gov = env.governing[key]
        assert gov.dtype == np.uint16
        assert np.array_equal(np.take_along_axis(stack, gov[0][None].astype(int), 0)[0], env.maximum[key])
        assert np.array_equal(np.take_along_axis(stack, gov[1][None].astype(int), 0)[0], env.minimum[key])
    rng = env.scalar_range()
    assert set(rng) == {"SIGXX", "SIGYY", "TAUXY", "VONMISES"}
