"""Merging one field's superelement parts: the same rows as stacking them, in less memory."""

from __future__ import annotations

import dataclasses
import gc
import tracemalloc

import numpy as np
import pytest

from ada.fem.formats.sesam.results.assembly import _FieldPart, _merge_parts
from ada.fem.formats.sesam.results.property_fields import MATERIAL_FIELD
from ada.fem.results.field_data import ElementFieldData, FieldPresentation, NodalFieldData

_TURN = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])


def _nodal(rows: int, seed: int) -> NodalFieldData:
    rng = np.random.default_rng(seed)
    values = np.column_stack((np.arange(1, rows + 1, dtype=float), rng.normal(size=(rows, 6))))
    return NodalFieldData("disp", 1, ["U1", "U2", "U3", "U4", "U5", "U6"], values)


def _stacked(parts: list[_FieldPart]) -> np.ndarray:
    """Each part copied, placed and stacked: the merge's reference."""
    out = []
    for p in parts:
        v = np.asarray(p.field.values, dtype=float).copy()
        v[:, 0] += p.base
        if p.rotation is not None and isinstance(p.field, NodalFieldData):
            v[:, 1:4] = v[:, 1:4] @ p.rotation.T
            v[:, 4:7] = v[:, 4:7] @ p.rotation.T
        if p.field.name == MATERIAL_FIELD:
            v[:, 2] = np.where(v[:, 2] > 0, v[:, 2] + p.base, v[:, 2])
        out.append(v)
    return np.vstack(out)


def test_merge_matches_stacking_with_ids_prefixed_and_vectors_turned():
    parts = [_FieldPart(_nodal(5, 0), 10_000_000, None), _FieldPart(_nodal(7, 1), 11_000_000, _TURN)]
    expected = _stacked(parts)

    merged = _merge_parts(list(parts))

    np.testing.assert_array_equal(merged.values, expected)
    assert merged.values.dtype == np.float64
    assert merged.name == "disp" and merged.components == parts[0].field.components


def test_a_single_part_is_placed_too():
    part = _FieldPart(_nodal(4, 2), 12_000_000, _TURN)
    expected = _stacked([part])

    np.testing.assert_array_equal(_merge_parts([part]).values, expected)


def test_material_ids_and_their_legend_are_prefixed_and_the_legends_joined():
    def material(labels, ids):
        values = np.column_stack((np.arange(1, len(ids) + 1), np.ones(len(ids)), ids)).astype(float)
        pres = FieldPresentation("material", ("props",), value_labels=labels)
        return ElementFieldData(MATERIAL_FIELD, 1, ["material"], values, presentation=pres)

    parts = [
        _FieldPart(material(((1.0, "mat_a"),), [1.0, 0.0]), 1_000_000, None),
        _FieldPart(material(((1.0, "mat_b"), (2.0, "mat_c")), [2.0, -1.0, 1.0]), 2_000_000, None),
    ]
    expected = _stacked(parts)

    merged = _merge_parts(list(parts))

    np.testing.assert_array_equal(merged.values, expected)
    assert merged.presentation.value_labels == (
        (1_000_001.0, "mat_a"),
        (2_000_001.0, "mat_b"),
        (2_000_002.0, "mat_c"),
    )


def test_parts_of_different_widths_are_refused():
    narrow = dataclasses.replace(_nodal(3, 3), values=np.ones((3, 4)))
    with pytest.raises(ValueError, match="columns"):
        _merge_parts([_FieldPart(_nodal(3, 4), 0, None), _FieldPart(narrow, 0, None)])


def _peak_above_baseline(fn) -> int:
    gc.collect()
    tracemalloc.start()
    try:
        base, _ = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        result = fn()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    del result
    return peak - base


def test_merge_peaks_at_about_one_merged_array_where_stacking_needs_two():
    """Stacking holds every placed copy and the stacked result at once; the merge fills one
    preallocated array and lets each part go once copied."""
    sizes = [40_000] * 8

    def make_parts():
        return [_FieldPart(_nodal(n, i), (i + 1) * 1_000_000, _TURN) for i, n in enumerate(sizes)]

    merged_bytes = sum(sizes) * 7 * 8

    holder = {"parts": make_parts()}
    expected = _stacked(holder["parts"])
    stacked_peak = _peak_above_baseline(lambda: _stacked(holder.pop("parts")))

    holder = {"parts": make_parts()}
    merged_peak = _peak_above_baseline(lambda: _merge_parts(holder.pop("parts")))

    holder = {"parts": make_parts()}
    np.testing.assert_array_equal(_merge_parts(holder.pop("parts")).values, expected)
    assert stacked_peak >= 1.9 * merged_bytes
    assert merged_peak < 1.2 * merged_bytes
