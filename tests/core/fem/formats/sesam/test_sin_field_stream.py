"""Reading a SIN a result case and RV card at a time, and comparing two results that way.

The streamed read must give exactly the fields of the full read, and the streamed comparison
exactly the statistics of :func:`compare_results` over two full reads.
"""

from __future__ import annotations

import hashlib
import shutil
import struct
from types import SimpleNamespace

import numpy as np
import pytest

from .sin_assembly import SIN_PATH, write_assembly_sin


def _field_key(f):
    return (
        type(f).__name__,
        f.name,
        int(f.step),
        str(getattr(f, "elem_type", None)),
        str(getattr(f, "field_pos", None)),
    )


def _digest(fields) -> list:
    return sorted(
        (
            _field_key(f),
            str(f.values.dtype),
            f.values.shape,
            hashlib.sha1(np.ascontiguousarray(f.values).tobytes()).hexdigest(),
            repr(f.presentation),
            list(f.components or []),
        )
        for f in fields
    )


@pytest.fixture(scope="module")
def assembly(tmp_path_factory):
    return write_assembly_sin(tmp_path_factory.mktemp("stream") / "ASSEMBLY.SIN")


@pytest.fixture(scope="module")
def perturbed(assembly, tmp_path_factory):
    """``assembly`` with some displacement words of its second superelement changed."""
    from ada.fem.formats.sesam.results.read_sin import _RV_VALUE_START
    from ada.fem.formats.sesam.results.sin_reader import open_sin

    out = tmp_path_factory.mktemp("stream_other") / "ASSEMBLY.SIN"
    shutil.copyfile(assembly, out)
    first_value = int(_RV_VALUE_START["RVNODDIS"]) - 1
    with open_sin(out, super_element="SEL11.IND1") as sf:
        ptrs = sf.type_blocks["RVNODDIS"].pointer_table
        words = [(int(p) + first_value) * 4 for p in ptrs[ptrs > 0][:5]]
    with open(out, "r+b") as fh:
        for byte in words:
            fh.seek(byte)
            (value,) = struct.unpack("<f", fh.read(4))
            fh.seek(byte)
            fh.write(struct.pack("<f", value * 1.5 + 1e-3))
    return out


def _stream(path, **kw):
    from ada.fem.formats.sesam.results.sin_field_stream import SinFieldStream

    return SinFieldStream.open(path, **kw)


@pytest.mark.parametrize("which", ["single", "assembly", "one_of_assembly"])
def test_the_chunks_are_the_fields_of_a_full_read(which, assembly):
    from ada.fem.formats.sesam.results.read_sin import read_sin_file

    path = SIN_PATH if which == "single" else assembly
    kw = {"super_element": "SEL11.IND1"} if which == "one_of_assembly" else {}
    full = read_sin_file(path, **kw)
    with _stream(path, **kw) as stream:
        chunks = list(stream.iter_chunks())
        header = stream.header

    assert [key for key, _ in chunks] == sorted(key for key, _ in chunks)
    for key, fields in chunks:
        assert {int(f.step) for f in fields} <= {key[0]}
    streamed = [f for _, fields in chunks for f in fields]
    assert _digest(streamed) == _digest(full.results)
    assert header.results == []
    assert header.name == full.name
    assert np.array_equal(header.mesh.nodes.identifiers, full.mesh.nodes.identifiers)
    assert np.array_equal(header.mesh.nodes.coords, full.mesh.nodes.coords)
    assert header.step_name_map == full.step_name_map
    assert getattr(header, "sesam_super_elements", None) == getattr(full, "sesam_super_elements", None)


def _comparison_digest(c) -> dict:
    def arr(x):
        return [None if np.isnan(v) else float(v) for v in np.asarray(x, dtype=float)]

    return {
        "diffs": sorted(
            (
                d.name,
                d.step,
                d.other_step,
                d.elem_type,
                tuple(d.components),
                d.num_points,
                d.num_unmatched,
                tuple(arr(d.max_abs_base)),
                tuple(arr(d.max_abs_other)),
                tuple(arr(d.max_abs_diff)),
                tuple(tuple(a) for a in d.max_diff_at),
            )
            for d in c.diffs
        ),
        "step_map": c.step_map,
        "only_in_base": sorted(c.only_in_base),
        "only_in_other": sorted(c.only_in_other),
        "stats": c.stats(max_rows=None),
    }


def test_a_streamed_comparison_has_the_statistics_of_a_full_one(assembly, perturbed):
    from ada.fem.formats.sesam.results.read_sin import read_sin_file
    from ada.fem.results.compare import compare_result_streams

    full = read_sin_file(assembly).compare(read_sin_file(perturbed))
    assert full.differing(), "the perturbation must show, or this compares nothing"
    with _stream(assembly) as base, _stream(perturbed) as other:
        streamed = compare_result_streams(base, other)

    assert _comparison_digest(streamed) == _comparison_digest(full)
    assert repr(streamed) == repr(full)


def test_a_result_compared_with_itself_is_read_once(assembly):
    from ada.fem.formats.sesam.results.read_sin import read_sin_file
    from ada.fem.results.compare import compare_result_streams

    result = read_sin_file(assembly)
    full = result.compare(result)
    with _stream(assembly) as stream:
        streamed = compare_result_streams(stream, None)

    assert _comparison_digest(streamed) == _comparison_digest(full)
    assert not streamed.differing()


def test_the_kept_difference_is_the_full_comparisons(assembly, perturbed):
    from ada.fem.formats.sesam.results.read_sin import read_sin_file
    from ada.fem.results.compare import compare_result_streams

    full = read_sin_file(assembly).compare(read_sin_file(perturbed)).to_fea_result()
    with _stream(assembly) as base, _stream(perturbed) as other:
        streamed = compare_result_streams(base, other, keep_difference=True).to_fea_result()

    assert _digest(streamed.results) == _digest(full.results)
    assert all(f.difference for f in streamed.results if not f.name.startswith("props."))
    assert streamed.name == full.name


def test_fields_split_differently_across_chunks_still_pair():
    """A field may sit in another chunk of the same step in the other result."""
    from ada.fem.results.common import FEAResult, FemNodes, Mesh
    from ada.fem.results.compare import compare_result_streams, compare_results
    from ada.fem.results.field_data import NodalFieldData

    def field(name, step, scale):
        values = np.column_stack((np.arange(1.0, 5.0), scale * np.arange(4.0)))
        return NodalFieldData(name, step, ["A"], values)

    mesh = Mesh(elements=[], nodes=FemNodes(coords=np.zeros((4, 3)), identifiers=np.arange(1, 5)))
    base_fields = [field("f_a", 1, 1.0), field("f_b", 1, 1.0), field("f_a", 2, 1.0), field("f_only", 2, 1.0)]
    other_fields = [field("f_b", 1, 2.0), field("f_a", 1, 1.0), field("f_a", 2, 3.0), field("f_c", 3, 1.0)]

    class Chunks:
        def __init__(self, name, chunks):
            self.header = FEAResult(name, "test", [], mesh)
            self._chunks = chunks

        def iter_chunks(self):
            yield from self._chunks

    base = Chunks("base", [((1, 0), base_fields[:1]), ((1, 1), base_fields[1:2]), ((2, 0), base_fields[2:])])
    other = Chunks(
        "other",
        [
            ((1, 0), other_fields[:1]),
            ((1, 1), other_fields[1:2]),
            ((2, 0), other_fields[2:3]),
            ((3, 0), other_fields[3:]),
        ],
    )

    streamed = compare_result_streams(base, other)
    full = compare_results(FEAResult("base", "test", base_fields, mesh), FEAResult("other", "test", other_fields, mesh))

    assert _comparison_digest(streamed) == _comparison_digest(full)
    assert streamed.only_in_base == [("f_only", 2, "")]
    assert streamed.only_in_other == []


# ── the case index: one pass over a block's case words, then a lookup per case ──


def _word_file(records):
    from ada.fem.formats.sesam.results.byte_source import MmapSource

    words = [0.0]
    pointers = []
    for data in records:
        words.append(float(len(data) + 1))
        pointers.append(len(words))
        words.extend(float(x) for x in data)
    return MmapSource(np.asarray(words, dtype=np.float32).tobytes()), np.asarray(pointers, dtype=np.int64)


@pytest.mark.parametrize(
    "records,extra_pointers",
    [
        ([[1, 10, 0.5, 1.5], [2, 10, 2.5, 3.5], [1, 11, 4.5, 5.5]], []),  # one width
        ([[1, 10, 0.5], [2, 10, 2.5, 3.5, 4.5], [1, 11, 5.5], [2, 12, 6.5, 7.5, 8.5]], []),  # one width per case
        ([[1, 10, 0.5], [1, 10, 2.5, 3.5], [2, 11, 5.5]], [0, 10_000]),  # ragged within a case, bad pointers
        ([[3]], []),  # a record with no value words
    ],
)
def test_the_case_index_gathers_what_the_scan_gathers(records, extra_pointers):
    from ada.fem.formats.sesam.results.sin_reader import SinFile

    source, pointers = _word_file(records)
    pointers = np.concatenate((pointers, np.asarray(extra_pointers, dtype=np.int64)))

    def fake(index):
        block = SimpleNamespace(pointer_table=pointers)
        return SimpleNamespace(source=source, type_blocks={"RVX": block}, index_cases=index)

    scan, indexed = fake(False), fake(True)
    for word in (1, 2, 3, 99):
        want = SinFile.gather_records(scan, "RVX", where_first_word=word)
        got = SinFile.gather_records(indexed, "RVX", where_first_word=word)
        assert (want is None) == (got is None), word
        if want is not None:
            assert want.shape == got.shape and np.array_equal(want, got), word
        want_n, want_words = SinFile.gather_ragged_records(scan, "RVX", where_first_word=word)
        got_n, got_words = SinFile.gather_ragged_records(indexed, "RVX", where_first_word=word)
        assert want_n.tolist() == got_n.tolist() and want_words.tolist() == got_words.tolist(), word


def test_rdpoints_per_row_matches_a_lookup_by_element():
    from ada.fem.formats.sesam.results.read_sif import _RdpointsLookup, _rdpoints_per_row

    nsp_i, eltyp_i = 5, 4
    rows = [
        [9.0, 0.0, 7.0, 0.0, 24.0, 4.0],
        [9.0, 0.0, 3.0, 0.0, 15.0, 2.0],
        [9.0, 0.0, 7.0, 0.0, 28.0, 8.0],  # a later record of element 7 wins
    ]
    rdpoints_map = {r[2]: r for r in rows}
    elno = np.array([7, 3, 5, 7, 3], dtype=np.int64)

    for lookup in (
        _RdpointsLookup.from_map(rdpoints_map, nsp_i, eltyp_i),
        _RdpointsLookup.from_columns(
            np.array([r[2] for r in rows]), np.array([r[nsp_i] for r in rows]), np.array([r[eltyp_i] for r in rows])
        ),
    ):
        nsp, elem_type, listed = _rdpoints_per_row(elno, lookup)
        assert nsp.tolist() == [8, 2, -1, 8, 2]
        assert elem_type.tolist() == [28, 15, -1, 28, 15]
        assert listed.tolist() == [True, True, False, True, True]
