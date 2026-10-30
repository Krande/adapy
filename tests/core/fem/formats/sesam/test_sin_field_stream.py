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

from .sin_assembly import SIN_PATH, write_assembly_sin, write_two_case_sin


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
    from ada.fem.formats.sesam.results.read_sif import (
        _rdpoints_per_row,
        _RdpointsLookup,
    )

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


def _bake_digests(out) -> dict:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir()) if p.is_file()}


def _bake(path, out, monkeypatch, *, streamed: bool) -> dict:
    from ada.fem.results.artefacts import bake_fea_artefacts_from_source

    monkeypatch.delenv("ADA_FEA_SIN_FIELD_CACHE", raising=False)
    monkeypatch.setenv("ADA_FEA_SIN_STREAMER", "on" if streamed else "off")
    bake_fea_artefacts_from_source(path, out, src_key="s")
    return _bake_digests(out)


def test_a_streamed_bake_of_an_assembly_is_the_full_bake(assembly, monkeypatch, tmp_path):
    """Every superelement placed and merged, from a file or a range source alike."""
    from ada.fem.formats.sesam.results.byte_source import (
        FileRangeSource,
        PagedByteSource,
    )
    from ada.fem.formats.sesam.results.read_sin import SinStreamReader
    from ada.fem.results.artefacts import bake_artefacts

    full = _bake(assembly, tmp_path / "full", monkeypatch, streamed=False)
    assert _bake(assembly, tmp_path / "streamed", monkeypatch, streamed=True) == full
    with SinStreamReader(PagedByteSource(FileRangeSource(str(assembly)))) as reader:
        assert len(reader.read_mesh_geometry().points) == 2 * 403
        bake_artefacts(reader, tmp_path / "ranged", src="s")
    assert _bake_digests(tmp_path / "ranged") == full


def test_a_case_that_lists_fewer_elements_bakes_as_the_full_read(monkeypatch, tmp_path):
    path = write_two_case_sin(tmp_path / "TWO.SIN", from_case=2)
    full = _bake(path, tmp_path / "full", monkeypatch, streamed=False)
    assert _bake(path, tmp_path / "streamed", monkeypatch, streamed=True) == full


def test_a_later_case_that_lists_more_elements_is_refused_by_the_streamer(monkeypatch, tmp_path):
    path = write_two_case_sin(tmp_path / "TWO.SIN", from_case=1)
    assert _bake(path, tmp_path / "full", monkeypatch, streamed=False)
    with pytest.raises(RuntimeError, match="lists elements at step index 1 that the first case does not"):
        _bake(path, tmp_path / "streamed", monkeypatch, streamed=True)


def write_uneven_assembly_sin(out):
    """An assembly whose superelements store different result cases: the first cases 1
    and 2, the second case 1 only -- so case 2 lists only the first's elements. A deck
    from a run whose superelements were analysed for different load sets reads so."""
    two_cases = write_two_case_sin(out.with_name("TWO_CASES.SIN"), card="")
    return write_assembly_sin(out, two_cases, drop_blocks_in_second=(), second_source=SIN_PATH)


def test_a_case_a_superelement_does_not_store_bakes_as_the_full_read(monkeypatch, tmp_path):
    """The streamer lays every case out as the first; a later case that the second
    superelement does not store lists fewer elements, placed by label (no value there),
    not refused as an element order drift."""
    from ada.fem.formats.sesam.results.read_sin import SinStreamReader
    from ada.fem.formats.sesam.results.sin_reader import open_sin

    path = write_uneven_assembly_sin(tmp_path / "UNEVEN.SIN")
    with SinStreamReader(open_sin(str(path))) as reader:
        assert reader._steps == [1, 2]
        spec = next(s for s in reader.element_field_specs() if s.category != "property" and s.n_elements > 1)
        first, second = (list(esv.values) for esv in reader.iter_element_field_steps(spec))
        # Case 2 has values on the first superelement's elements only.
        assert not np.isnan(np.asarray(first)).any()
        lacking = np.isnan(np.asarray(second)).reshape(spec.n_elements, -1).all(axis=1)
        assert 0 < lacking.sum() < spec.n_elements
    full = _bake(path, tmp_path / "full", monkeypatch, streamed=False)
    assert _bake(path, tmp_path / "streamed", monkeypatch, streamed=True) == full


def _result_digest(result) -> str:
    h = hashlib.sha256()
    for f in result.results:
        v = np.ascontiguousarray(f.values)
        key = (
            type(f).__name__,
            f.name,
            int(f.step),
            str(getattr(f, "elem_type", None)),
            str(getattr(f, "field_pos", None)),
            str(v.dtype),
            v.shape,
            repr(f.presentation),
            list(f.components or []),
            f.eigen_freq,
            f.eigen_value,
        )
        h.update(repr(key).encode())
        h.update(v.tobytes())
    mesh = result.mesh
    for a in (mesh.nodes.coords, mesh.nodes.identifiers, mesh.nodes.labels):
        if a is not None:
            h.update(np.ascontiguousarray(a).tobytes())
    for b in mesh.elements:
        for a in (b.node_refs, b.identifiers, b.labels):
            if a is not None:
                h.update(np.ascontiguousarray(a).tobytes())
    metadata = (result.name, result.step_name_map, getattr(result, "sesam_case_names", None))
    h.update(repr((*metadata, getattr(result, "sesam_groups", None))).encode())
    return h.hexdigest()[:20]


#: What the full read (every case of every card at once, then merged) gave for each
#: fixture before the streamed read replaced it. The streamed read must give the same.
_FULL_READ_DIGESTS = {
    "single": "715e38f1c8fbd73fa470",
    "single_step1": "715e38f1c8fbd73fa470",
    "single_step9": "ea19cd68c8208865e61f",
    "assembly": "8120c8c6250395df2038",
    "assembly_sel11": "5907043ad9ba05a4c3d1",
    "assembly_both": "8120c8c6250395df2038",
    "assembly_phase": "8120c8c6250395df2038",
    "two_fewer": "b4ceb6504ecf0764bb8b",
    "two_more": "10b1312d5e9878aa540e",
    "two_more_step2": "7fe6d65f87b2f3601007",
}


@pytest.mark.parametrize(
    "which,kw",
    [
        ("single", {}),
        ("single_step1", {"step": 1}),
        ("single_step9", {"step": 9}),
        ("assembly", {}),
        ("assembly_sel11", {"super_element": "SEL11.IND1"}),
        ("assembly_both", {"super_element": ["SEL10.IND1", "SEL11.IND1"]}),
        ("assembly_phase", {"complex_phase": 0.5}),
        ("two_fewer", {}),
        ("two_more", {}),
        ("two_more_step2", {"step": 2}),
    ],
)
def test_a_read_is_what_the_full_read_was(which, kw, assembly, tmp_path):
    from ada.fem.formats.sesam.results.read_sin import read_sin_file

    if which.startswith("single"):
        path = SIN_PATH
    elif which.startswith("assembly"):
        path = assembly
    else:
        fewer = which == "two_fewer"
        path = write_two_case_sin(
            tmp_path / ("TWO_FEWER.SIN" if fewer else "TWO_MORE.SIN"), from_case=2 if fewer else 1
        )
    assert _result_digest(read_sin_file(path, **kw)) == _FULL_READ_DIGESTS[which]


class _FileFetcher:
    """The host's range fetcher, over a local file."""

    def __init__(self, path):
        self._data = path.read_bytes()

    def size(self) -> int:
        return len(self._data)

    def fetch(self, offset: int, length: int) -> bytes:
        return self._data[offset : offset + length]

    def close(self) -> None:
        pass


@pytest.mark.parametrize("which", ["single", "assembly"])
def test_the_browser_read_over_a_range_fetcher_is_the_read(which, assembly):
    from ada.cadit.wasm_convert import _streamed_sin_result
    from ada.fem.formats.sesam.results.read_sin import read_sin_file

    path = SIN_PATH if which == "single" else assembly
    whole = read_sin_file(path)
    ranged = _streamed_sin_result(_FileFetcher(path))
    assert _digest(ranged.results) == _digest(whole.results)
    assert len(ranged.mesh.nodes.identifiers) == len(whole.mesh.nodes.identifiers)
    first = _streamed_sin_result(_FileFetcher(path), pick_first_step=True)
    assert _digest(first.results) == _digest(read_sin_file(path, step=1).results)
