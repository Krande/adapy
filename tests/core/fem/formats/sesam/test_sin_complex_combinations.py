"""Result combinations with complex basic cases and phase angles.

A Sesam results file may hold COMPLEX basic result cases (RDRESREF COMPLX=1,
e.g. quasi-static wave cases) whose RV* records carry a real and an imaginary
word per component, interleaved ``R1, I1, R2, I2, …`` (Results Interface File
4.3.1.35 / 4.3.1.36 / 4.3.1.29 / 4.3.1.49). A combination term is
``(basic case, FACT, PHASE)`` with PHASE in radians (4.3.1.11), and contributes
by Table 4.1 of 4.3.1.1:

    real combination:     [R cos Φ − I sin Φ]·FACT          (I = 0 for a real case)
    complex combination:  [R cos Φ − I sin Φ, I cos Φ + R sin Φ]·FACT

Synthetic rows only — no binary fixture needed.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from ada.fem.formats.sesam.read import cards
from ada.fem.formats.sesam.results.read_sin import (
    CombinationRecipe,
    SinReader,
    _accumulate_rv_combination,
    _present_complex_rows,
    read_complex_result_cases,
    read_result_combination_terms,
    read_result_combinations,
)

R = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], dtype=np.float32)
I = np.array([100.0, 200.0, 300.0, 400.0, 500.0, 600.0], dtype=np.float32)  # noqa: E741


def _interleave(re, im):
    out = np.empty(2 * len(re), dtype=np.float64)
    out[0::2] = re
    out[1::2] = im
    return out


def _noddis(step, values, *, inod=10, nrows=1):
    """A vectorised RVNODDIS table: super-header row, then ``nrows`` data rows."""
    values = np.asarray(values, dtype=np.float64)
    width = 5 + len(values)
    table = np.zeros((nrows + 1, width))
    table[0, :3] = [-1, 1, nrows]
    for k in range(nrows):
        table[k + 1, :5] = [width, step, inod + k, 6, 0]
        table[k + 1, 5:] = values
    return table


def _f32(x):
    return np.float32(x)


# ── _accumulate_rv_combination: the Table 4.1 cases ─────────────────────────


def test_real_terms_at_zero_phase_keep_the_old_float32_arithmetic():
    """Old signature, real cases: bit-for-bit ``f32(v)·f32(F)`` accumulated in float32."""
    a = _noddis(1, R)
    b = _noddis(2, I)
    out = _accumulate_rv_combination(cards.RVNODDIS, None, a, 1.2, combination_step=9)
    out = _accumulate_rv_combination(cards.RVNODDIS, out, b, -0.5, combination_step=9)

    expected = R * _f32(1.2)
    expected += I * _f32(-0.5)
    assert np.array_equal(out[1, 5:], expected.astype(np.float64))
    assert np.array_equal(out[1, :5], [11, 9, 10, 6, 0])


def test_complex_case_at_zero_phase_contributes_its_real_part():
    table = _noddis(60, _interleave(R, I))
    out = _accumulate_rv_combination(
        cards.RVNODDIS, None, table, 2.0, combination_step=20, phase=0.0, basic_complex=True
    )
    assert out.shape == (2, 11)
    assert np.array_equal(out[1, 5:], (R * 2).astype(np.float64))
    # NFIELD counts the words the combined record now holds.
    assert out[1, 0] == 11
    assert out[1, 1] == 20


def test_complex_case_at_quarter_phase_contributes_minus_its_imaginary_part():
    table = _noddis(60, _interleave(R, I))
    out = _accumulate_rv_combination(
        cards.RVNODDIS, None, table, 1.0, combination_step=20, phase=math.pi / 2, basic_complex=True
    )
    # cos(π/2) is 6e-17 in double, not 0, so R leaves a trace far below float32 noise.
    assert np.allclose(out[1, 5:], -I, rtol=1e-6, atol=1e-6)


def test_complex_case_at_thirty_degrees():
    phase = 0.5236  # as stored on a results file for 30°
    table = _noddis(60, _interleave(R, I))
    out = _accumulate_rv_combination(
        cards.RVNODDIS, None, table, 12.5, combination_step=21, phase=phase, basic_complex=True
    )
    expected = (R.astype(float) * math.cos(phase) - I.astype(float) * math.sin(phase)) * 12.5
    assert np.allclose(out[1, 5:], expected, rtol=1e-6)


def test_real_case_with_a_phase_contributes_r_cos_phi():
    table = _noddis(3, R)
    out = _accumulate_rv_combination(cards.RVNODDIS, None, table, 2.0, combination_step=9, phase=math.pi / 3)
    assert np.allclose(out[1, 5:], R * 2.0 * 0.5, rtol=1e-6)


def test_interleaved_words_land_on_their_components():
    """R1, I1, R2, I2, … — not six reals followed by six imaginaries."""
    table = _noddis(60, _interleave(R, I))
    real = _accumulate_rv_combination(cards.RVNODDIS, None, table.copy(), 1.0, combination_step=20, basic_complex=True)
    imag = _accumulate_rv_combination(
        cards.RVNODDIS, None, table.copy(), 1.0, combination_step=20, phase=-math.pi / 2, basic_complex=True
    )
    assert np.array_equal(real[1, 5:], [1, 2, 3, 4, 5, 6])
    assert np.allclose(imag[1, 5:], [100, 200, 300, 400, 500, 600], rtol=1e-6)


def test_real_and_complex_cases_superpose_into_a_real_combination():
    phase = 0.5236
    real_case = _noddis(1, R)
    wave = _noddis(60, _interleave(R, I))
    out = _accumulate_rv_combination(cards.RVNODDIS, None, real_case, 1.3, combination_step=21)
    out = _accumulate_rv_combination(
        cards.RVNODDIS, out, wave, 12.5, combination_step=21, phase=phase, basic_complex=True, reference_step=1
    )
    c, s = _f32(12.5 * math.cos(phase)), _f32(12.5 * math.sin(phase))
    expected = R * _f32(1.3)
    expected += R * c - I * s
    assert np.array_equal(out[1, 5:], expected.astype(np.float64))


def test_complex_combination_follows_table_4_1():
    phase = 0.7
    c, s = math.cos(phase), math.sin(phase)
    real_case = _noddis(1, R)
    wave = _noddis(60, _interleave(R, I))
    out = _accumulate_rv_combination(
        cards.RVNODDIS, None, real_case, 2.0, combination_step=22, phase=phase, combination_complex=True
    )
    assert out.shape == (2, 17) and out[1, 0] == 17
    assert np.allclose(out[1, 5::2], 2 * R * c, rtol=1e-6)
    assert np.allclose(out[1, 6::2], 2 * R * s, rtol=1e-6)

    out = _accumulate_rv_combination(
        cards.RVNODDIS, out, wave, 1.0, combination_step=22, phase=phase, basic_complex=True, combination_complex=True
    )
    assert np.allclose(out[1, 5::2], 2 * R * c + (R * c - I * s), rtol=1e-6)
    assert np.allclose(out[1, 6::2], 2 * R * s + (I * c + R * s), rtol=1e-6)


def test_a_differing_boundary_condition_reference_is_not_a_different_entity():
    """RVNODREA's IRBOC is a per-case descriptor (4.3.1.36), not the row's identity."""
    a = np.zeros((2, 12))
    a[1] = [12, 1, 10, 7, 14, 0, *R]
    b = np.zeros((2, 18))
    b[1] = [18, 60, 10, 7, 44, 0, *_interleave(R, I)]
    out = _accumulate_rv_combination(cards.RVNODREA, None, a, 1.0, combination_step=20)
    out = _accumulate_rv_combination(cards.RVNODREA, out, b, 1.0, combination_step=20, basic_complex=True)
    assert np.array_equal(out[1, 6:], (R * 2).astype(np.float64))


def test_a_differing_node_is_refused_and_named():
    a = _noddis(1, R, inod=10)
    b = _noddis(2, R, inod=11)
    out = _accumulate_rv_combination(cards.RVNODDIS, None, a, 1.0, combination_step=20)
    with pytest.raises(ValueError, match=r"RVNODDIS combination 20: basic case 2 and basic case 1 disagree on INOD"):
        _accumulate_rv_combination(cards.RVNODDIS, out, b, 1.0, combination_step=20, reference_step=1)


def test_a_differing_element_is_refused_on_the_ragged_path_too():
    a = [[0.0] * 5, [11.0, 1.0, 5087.0, 1.0, 3.0, *R.tolist()]]
    b = [[0.0] * 5, [11.0, 2.0, 5088.0, 1.0, 3.0, *R.tolist()]]
    out = _accumulate_rv_combination(cards.RVSTRESS, None, a, 1.0, combination_step=9)
    with pytest.raises(ValueError, match="disagree on IIELNO"):
        _accumulate_rv_combination(cards.RVSTRESS, out, b, 1.0, combination_step=9, reference_step=1)


def test_ragged_and_vectorised_paths_agree():
    phase = 0.5236
    real_case = _noddis(1, R, nrows=3)
    wave = _noddis(60, _interleave(R, I), nrows=3)
    wave[2, 5:] *= 3  # rows differ, so a row mix-up would show

    vec = _accumulate_rv_combination(cards.RVNODDIS, None, real_case, 0.8, combination_step=21)
    vec = _accumulate_rv_combination(
        cards.RVNODDIS, vec, wave, 12.5, combination_step=21, phase=phase, basic_complex=True
    )
    rag = _accumulate_rv_combination(cards.RVNODDIS, None, real_case.tolist(), 0.8, combination_step=21)
    rag = _accumulate_rv_combination(
        cards.RVNODDIS, rag, wave.tolist(), 12.5, combination_step=21, phase=phase, basic_complex=True
    )
    assert np.array_equal(np.asarray(rag[1:]), vec[1:])

    # And one case vectorised, the other per record (a table ragged across cases).
    mixed = _accumulate_rv_combination(cards.RVNODDIS, None, real_case, 0.8, combination_step=21)
    mixed = _accumulate_rv_combination(
        cards.RVNODDIS, mixed, wave.tolist(), 12.5, combination_step=21, phase=phase, basic_complex=True
    )
    assert np.array_equal(np.asarray(mixed[1:]), vec[1:])


def test_ragged_rows_of_different_widths_keep_their_own_values():
    """RVSTRESS: widths vary by element descriptor; the flat de-interleave must not bleed across rows."""
    short_r, short_i = [1.0, 2.0], [10.0, 20.0]
    long_r, long_i = [3.0, 4.0, 5.0], [30.0, 40.0, 50.0]
    wave = [
        [0.0] * 5,
        [9.0, 60.0, 1.0, 1.0, 1.0, *_interleave(short_r, short_i)],
        [11.0, 60.0, 2.0, 1.0, 2.0, *_interleave(long_r, long_i)],
    ]
    real_case = [
        [0.0] * 5,
        [7.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
        [8.0, 1.0, 2.0, 1.0, 2.0, 1.0, 1.0, 1.0],
    ]
    out = _accumulate_rv_combination(cards.RVSTRESS, None, wave, 1.0, combination_step=9, basic_complex=True)
    assert out[1] == [7.0, 9.0, 1.0, 1.0, 1.0, 1.0, 2.0]
    assert out[2] == [8.0, 9.0, 2.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    out = _accumulate_rv_combination(cards.RVSTRESS, out, real_case, 2.0, combination_step=9)
    assert out[1][5:] == [3.0, 4.0]
    assert out[2][5:] == [5.0, 6.0, 7.0]


def test_a_complex_case_with_an_odd_word_count_is_refused():
    table = _noddis(60, np.arange(7.0))
    with pytest.raises(ValueError, match="odd count"):
        _accumulate_rv_combination(cards.RVNODDIS, None, table, 1.0, combination_step=20, basic_complex=True)


# ── presenting a complex case read on its own ───────────────────────────────


def test_a_complex_case_is_presented_as_its_real_part_by_default():
    table = _noddis(60, _interleave(R, I), nrows=2)
    out = _present_complex_rows(cards.RVNODDIS, table, frozenset({60}), 0.0)
    assert out.shape == (3, 11)
    assert np.array_equal(out[1:, 5:], np.tile(R, (2, 1)).astype(np.float64))
    assert np.all(out[1:, 0] == 11)
    assert np.array_equal(out[1:, 1:5], table[1:, 1:5])


def test_a_complex_case_can_be_presented_at_another_phase():
    table = _noddis(60, _interleave(R, I))
    imag = _present_complex_rows(cards.RVNODDIS, table, frozenset({60}), -math.pi / 2)
    assert np.allclose(imag[1, 5:], I, rtol=1e-6)


def test_real_rows_pass_through_and_mixed_tables_are_split():
    real_case = _noddis(1, R)
    assert _present_complex_rows(cards.RVNODDIS, real_case, frozenset({60}), 0.0) is real_case

    rows = [
        [-1.0, 1.0, 2.0],
        [11.0, 1.0, 10.0, 6.0, 0.0, *R.tolist()],
        [17.0, 60.0, 10.0, 6.0, 0.0, *_interleave(R, I)],
    ]
    out = _present_complex_rows(cards.RVNODDIS, rows, frozenset({60}), 0.0)
    assert out[1] is rows[1]
    assert out[2] == [11.0, 60.0, 10.0, 6.0, 0.0, *R.tolist()]


# ── reading the recipes ─────────────────────────────────────────────────────


class _FakeSin:
    def __init__(self, **records):
        self.records = records
        self.type_blocks = {name: object() for name in records}

    def iter_records(self, name, **_):
        yield from self.records[name]


def _rdrescmb(ires, complx, terms):
    return [float(ires), float(complx), float(len(terms)), *[float(x) for t in terms for x in t]]


def test_read_result_combination_terms_keeps_phases_and_drops_zero_factors():
    sin = _FakeSin(
        RDRESCMB=[
            _rdrescmb(20, 0, [(11, 0, 0), (12, 0, 0), (13, 0.8, 0), (60, 12.5, 0.0), (14, 1.3, 0)]),
            _rdrescmb(21, 0, [(11, 0, 0), (12, 0, 0), (13, 0.8, 0), (60, 12.5, 0.5236), (14, 1.3, 0)]),
            _rdrescmb(22, 1, [(60, 1.0, 0.0), (60, 1.0, 1.0472)]),
        ]
    )
    terms = read_result_combination_terms(sin)

    assert terms[20] == CombinationRecipe(complex=False, terms=((13, 0.8, 0.0), (60, 12.5, 0.0), (14, 1.3, 0.0)))
    assert terms[21].terms[1] == (60, 12.5, 0.5236)
    assert terms[20] != terms[21]
    # The same basic case twice at two phases stays two terms.
    assert terms[22].complex is True
    assert terms[22].terms == ((60, 1.0, 0.0), (60, 1.0, 1.0472))

    legacy = read_result_combinations(sin)
    assert legacy[20] == {13: 0.8, 60: 12.5, 14: 1.3}
    assert legacy[22] == {60: 2.0}


def test_read_complex_result_cases_uses_the_rdresref_flag():
    sin = _FakeSin(
        RDRESREF=[
            [1.0, 1.0, 1.0, 0.0, 0.0, 1.0, 10.0, 1.0, 0.0],
            [60.0, 3.0, 1.0, 6.0, 1.0, 1.0, 10.0, 1.0, 0.0],
            [20.0, 1.0, 1.0, 100.0, 0.0, 0.0],
        ]
    )
    assert read_complex_result_cases(sin) == frozenset({60})


def test_legacy_dict_recipe_is_a_real_zero_phase_combination():
    assert CombinationRecipe.coerce({1: 1.2, 2: 0.5}) == CombinationRecipe(False, ((1, 1.2, 0.0), (2, 0.5, 0.0)))
    assert not CombinationRecipe(False, ())


# ── SinReader.load_combination end to end, on stubbed card reads ────────────


def _stub_reader(tables, complex_cases):
    reader = SinReader(sin=None)
    reader._static_loaded = True
    reader._static_results = []
    reader._complex_cases = frozenset(complex_cases)

    def read(card, step, *, raw=False, ragged=False):
        if card.name != "RVNODDIS" or step not in tables:
            return None
        rows = tables[step].copy()
        if not raw:
            rows = _present_complex_rows(card, rows, reader._complex_cases & {step}, reader.complex_phase)
        elif ragged and isinstance(rows, list):
            # What the real reader hands the superposition for a per-record table.
            from ada.fem.formats.sesam.results.read_sin import _RaggedRows

            rows = _RaggedRows.from_rows(rows, 5)
        return (card.name, rows)

    reader._read_result_card = read
    return reader


def test_load_combination_applies_each_terms_phase():
    tables = {1: _noddis(1, R), 60: _noddis(60, _interleave(R, I))}
    reader = _stub_reader(tables, {60})

    recipe = CombinationRecipe(False, ((1, 1.0, 0.0), (60, 2.0, 0.0), (60, 2.0, math.pi / 2)))
    reader.load_combination(21, recipe)
    (name, rows) = reader.results[0]
    assert name == "RVNODDIS"
    # 1·R + 2·R (phase 0) + 2·(−I) (phase 22°): the duplicate basic is NOT summed as 4·R.
    assert np.allclose(rows[1, 5:], R + 2 * R - 2 * I, rtol=1e-6)
    assert rows[1, 1] == 21

    # The legacy dict form still works for real cases.
    reader.load_combination(89, {1: 2.0})
    assert np.array_equal(reader.results[0][1][1, 5:], (R * 2).astype(np.float64))


def test_a_complex_basic_case_read_on_its_own_presents_six_components():
    tables = {60: _noddis(60, _interleave(R, I))}
    reader = _stub_reader(tables, {60})
    _, rows = reader._read_result_card(cards.RVNODDIS, 60)
    assert rows.shape[1] == 11
    assert np.array_equal(rows[1, 5:], R.astype(np.float64))


# ── the reader around it ────────────────────────────────────────────────────


def _word_file(records):
    """A tiny in-memory SIN word stream: ``[NFIELD, *data]`` per record → (source, pointers)."""
    from ada.fem.formats.sesam.results.byte_source import MmapSource

    words = [0.0]
    pointers = []
    for data in records:
        words.append(float(len(data) + 1))
        pointers.append(len(words))
        words.extend(float(x) for x in data)
    return MmapSource(np.asarray(words, dtype=np.float32).tobytes()), np.asarray(pointers, dtype=np.int64)


def test_one_case_of_a_table_ragged_across_cases_still_vectorises():
    from types import SimpleNamespace

    from ada.fem.formats.sesam.results.sin_reader import SinFile

    real = [[1, 10, 6, 0, *R.tolist()], [1, 11, 6, 0, *R.tolist()]]
    wave = [[60, 10, 6, 0, *_interleave(R, I)], [60, 11, 6, 0, *_interleave(R, I)]]
    source, pointers = _word_file(real + wave)
    fake = SimpleNamespace(source=source, type_blocks={"RVNODDIS": SimpleNamespace(pointer_table=pointers)})

    assert SinFile.gather_records(fake, "RVNODDIS") is None  # the whole table is ragged
    one = SinFile.gather_records(fake, "RVNODDIS", where_first_word=60)
    assert one.shape == (2, 17)
    assert np.array_equal(one[:, 5:], np.tile(_interleave(R, I), (2, 1)))
    assert SinFile.gather_records(fake, "RVNODDIS", where_first_word=1).shape == (2, 11)
    assert SinFile.gather_records(fake, "RVNODDIS", where_first_word=99) is None


def test_filtered_iter_records_yields_what_filtering_every_record_yields():
    # The vectorised pre-filter must keep the loop's own order and skips: ragged
    # widths, a zero pointer, a pointer past the file, and a 1-word record that
    # has no second word to test.
    from types import SimpleNamespace

    from ada.fem.formats.sesam.results.sin_reader import SinFile

    records = [
        [1, 10, 6, 0, 1.5],
        [2, 10, 6, 0, 2.5, 3.5, 4.5],
        [1, 11, 6, 0, 5.5, 6.5, 7.5],
        [2, 12, 6, 0, 8.5],
        [1, 12, 6, 0, 9.5, 10.5],
        [1],
    ]
    source, pointers = _word_file(records)
    pointers = np.concatenate((pointers[:2], [0], pointers[2:], [10_000]))
    fake = SimpleNamespace(source=source, type_blocks={"RVFORCES": SimpleNamespace(pointer_table=pointers)})

    everything = list(SinFile.iter_records(fake, "RVFORCES"))
    assert len(everything) == len(records)
    for first in (1, 2, 3):
        for second in (None, {10}, {11, 12}, {99}):
            got = list(SinFile.iter_records(fake, "RVFORCES", where_first_word=first, where_second_word=second))
            want = [
                r
                for r in everything
                if int(r[0]) == first and (second is None or (len(r) >= 2 and int(r[1]) in second))
            ]
            assert got == want, (first, second)
    got = list(SinFile.iter_records(fake, "RVFORCES", where_second_word={12}))
    assert got == [r for r in everything if len(r) >= 2 and int(r[1]) == 12]

    # The array form yields the same records, and makes the same _RaggedRows.
    from ada.fem.formats.sesam.results.read_sin import _RaggedRows

    for first in (None, 1, 2, 3):
        for second in (None, {10}, {11, 12}):
            records = list(SinFile.iter_records(fake, "RVFORCES", where_first_word=first, where_second_word=second))
            n_data, words = SinFile.gather_ragged_records(
                fake, "RVFORCES", where_first_word=first, where_second_word=second
            )
            assert n_data.tolist() == [len(r) for r in records]
            assert words.tolist() == [x for r in records for x in r]
            rows = [[9.0], *[[float(len(r) + 1), *r] for r in records if len(r) + 1 >= 5]]
            if len(rows) == len(records) + 1:
                ragged = _RaggedRows.from_records([9.0], n_data, words, 5)
                listed = _RaggedRows.from_rows(rows, 5)
                assert ragged.to_rows() == listed.to_rows() == rows


@pytest.mark.parametrize("per_record", [False, True], ids=["vectorised", "per-record"])
def test_unstored_combinations_read_each_basic_case_once_and_match_one_by_one(per_record):
    arrays = {1: _noddis(1, R, nrows=2), 2: _noddis(2, 3 * R + 0.1, nrows=2)}
    # Per record: the row lists a ragged table is read as, superposed as arrays
    # and turned back into rows -- the same numbers as the vectorised path.
    tables = {k: (v.tolist() if per_record else v) for k, v in arrays.items()}
    recipes = {
        10: CombinationRecipe(False, ((1, 1.3, 0.0), (2, 0.7, 0.0))),
        11: CombinationRecipe(False, ((2, 2.0, 0.0), (1, 1.1, 0.0), (1, 3.0, 0.0))),
        12: CombinationRecipe(False, ((1, 0.9, 0.0),)),
    }
    reader = _stub_reader(tables, set())
    calls = []
    read = reader._read_result_card

    def counting(card, step, *, raw=False, ragged=False):
        calls.append((card.name, step))
        return read(card, step, raw=raw, ragged=ragged)

    reader._read_result_card = counting
    reader._combination_terms = recipes
    reader.stored_steps = lambda: {1, 2}
    stored = np.vstack((arrays[1], arrays[2][1:]))
    reader.results = [("RVNODDIS", stored.tolist() if per_record else stored.copy())]

    reader.append_unstored_combinations()

    # One read per basic case and card, however many terms name it.
    assert len(calls) == len(set(calls))
    assert sorted(step for name, step in calls if name == "RVNODDIS") == [1, 2]
    ((name, rows),) = reader.results
    assert name == "RVNODDIS"
    assert isinstance(rows, list) == per_record
    rows = np.asarray(rows, dtype=np.float64)
    assert np.array_equal(rows[: stored.shape[0]], stored)
    # Each combination's rows, in IRES order, exactly as building it on its own does.
    at = stored.shape[0]
    for ires in sorted(recipes):
        alone = _stub_reader(arrays, set())
        alone.load_combination(ires, recipes[ires])
        expected = alone.results[0][1][1:]
        assert np.array_equal(rows[at : at + len(expected)], expected), ires
        at += len(expected)
    assert at == rows.shape[0]


def test_rv_tables_concatenate_like_one_at_a_time():
    from ada.fem.formats.sesam.results.read_sin import _concat_rv_tables

    a, b, c = _noddis(1, R, nrows=2), _noddis(2, R, nrows=1), _noddis(3, R, nrows=3)
    joined = _concat_rv_tables(a, [b, c[:1], c])
    assert isinstance(joined, np.ndarray)
    assert np.array_equal(joined, np.vstack((a, b[1:], c[1:])))
    # From the first per-record table on, rows are a list.
    ragged = [[-2.0, 2.0, 1.0], [7.0, 4.0, 1.0, 2.0]]
    mixed = _concat_rv_tables(a, [b, ragged, c])
    assert isinstance(mixed, list)
    assert len(mixed) == 3 + 1 + 1 + 3
    assert mixed[-4] == ragged[1]
    with pytest.raises(ValueError, match="width"):
        _concat_rv_tables(a, [_noddis(4, np.ones(3), nrows=1)])


def test_stale_pointers_past_a_packed_2d_extent_are_trimmed():
    from ada.fem.formats.sesam.results.sin_reader import _trim_stale_packed_tail

    # 3 cases x 4 entities, packed as 3*100000 + 4; then stale capacity slots.
    table = np.array([5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 0, 0, 99, 0, 98], dtype=np.int64)
    trimmed = _trim_stale_packed_tail(table, 2, [300004])
    assert trimmed.tolist() == table[:13].tolist()

    # Left alone: a live pointer right after the extent (a plain count misread as
    # packed), a small plain dim, a 2-D dim, or another table kind.
    dense = np.arange(1, 20, dtype=np.int64)
    assert _trim_stale_packed_tail(dense, 2, [300004]) is dense
    assert _trim_stale_packed_tail(table, 2, [40]) is table
    assert _trim_stale_packed_tail(table, 2, [3, 4]) is table
    assert _trim_stale_packed_tail(table, 21, [300004]) is table

    # Left alone: exactly the extent, or the extent plus one slot.
    exact = table[:12]
    assert _trim_stale_packed_tail(exact, 2, [300004]) is exact
    plus_one = table[:13]
    assert _trim_stale_packed_tail(plus_one, 2, [300004]) is plus_one

    # Left alone: an extent that would fall inside the real table. A 2-case
    # table of 100003 entities "packs" to 3*3 = 9; a zero at slot 9 of a sparse
    # table must not cut the 200000 slots after it.
    sparse = np.arange(1, 200_007, dtype=np.int64)
    sparse[9] = 0
    assert _trim_stale_packed_tail(sparse, 2, [2 * 100_000 + 100_003]) is sparse


def test_result_name_map_calls_an_unnamed_case_by_its_number():
    from types import SimpleNamespace

    from ada.fem.formats.sesam.results.read_sif import Sif2Mesh

    sif = SimpleNamespace(
        get_tdresref=lambda: {1: [3.0, 1.0, 0.0, 0.0, "dead"]},
        get_rdresref=lambda: {1: [9, 1], 20: [9, 20]},
    )
    assert Sif2Mesh.get_result_name_map(SimpleNamespace(sif=sif)) == {1: "dead", 20: 20}


# ── labels ──────────────────────────────────────────────────────────────────


def test_combination_label_shows_non_zero_phases():
    from ada.fem.formats.sesam.results.case_names import combination_label

    names = {13: "dead", 60: "wave"}
    at_0 = CombinationRecipe(False, ((13, 0.8, 0.0), (60, 12.5, 0.0)))
    at_30 = CombinationRecipe(False, ((13, 0.8, 0.0), (60, 12.5, 0.5236)))
    assert combination_label(at_0, names) == "0.8·dead + 12.5·wave"
    assert combination_label(at_30, names) == "0.8·dead + 12.5·wave∠30°"
    assert combination_label({13: 0.8}, names) == "0.8·dead"
