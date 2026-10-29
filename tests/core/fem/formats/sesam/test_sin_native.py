"""adacpp's SIN loops (``adacpp.fea.sin``) against the numpy path they replace.

Every test reads the same file twice, once with ``ADA_SIN_NATIVE=0``, and requires the same bytes:
every streamed chunk and the mesh, and every comparison statistic. The rest of the suite runs on
whichever path the installed adacpp gives.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pytest

from ada.fem.results import sin_native

from . import test_sin_field_stream as _stream_tests
from .sin_assembly import SIN_PATH

_comparison_digest = _stream_tests._comparison_digest
_word_file = _stream_tests._word_file
# The stream tests' fixtures, shared.
assembly = _stream_tests.assembly
perturbed = _stream_tests.perturbed

pytestmark = pytest.mark.skipif(not sin_native.available(), reason="adacpp without fea.sin")


def _bytes_digest(fields) -> list:
    out = []
    for f in fields:
        v = np.ascontiguousarray(f.values)
        out.append(
            (
                type(f).__name__,
                f.name,
                int(f.step),
                str(getattr(f, "elem_type", None)),
                str(getattr(f, "field_pos", None)),
                str(v.dtype),
                v.shape,
                hashlib.sha256(v.tobytes()).hexdigest(),
                repr(f.presentation),
                list(f.components or []),
            )
        )
    return out


def _mesh_digest(mesh) -> str:
    h = hashlib.sha256()
    for part in (mesh.nodes, mesh.elements):
        for name in ("coords", "identifiers", "ids", "connectivity", "elem_types"):
            a = getattr(part, name, None)
            if isinstance(a, np.ndarray):
                h.update(name.encode() + str(a.dtype).encode() + repr(a.shape).encode() + a.tobytes())
    return h.hexdigest()


def _streamed(path, monkeypatch, native: bool):
    from ada.fem.formats.sesam.results.sin_field_stream import SinFieldStream

    monkeypatch.setenv("ADA_SIN_NATIVE", "1" if native else "0")
    with SinFieldStream.open(path) as s:
        chunks = [(key, _bytes_digest(fields)) for key, fields in s.iter_chunks()]
        return chunks, _mesh_digest(s.header.mesh)


@pytest.mark.parametrize("which", ["single", "assembly"])
def test_streamed_chunks_are_bit_identical(which, assembly, monkeypatch):
    path = SIN_PATH if which == "single" else assembly
    native = _streamed(path, monkeypatch, True)
    numpy_path = _streamed(path, monkeypatch, False)
    assert native[0], "nothing streamed"
    assert native == numpy_path


@pytest.mark.parametrize("other", ["itself", "perturbed"])
def test_streamed_comparison_is_identical(other, assembly, perturbed, monkeypatch):
    from ada.fem.formats.sesam.results.sin_field_stream import SinFieldStream
    from ada.fem.results.compare import compare_result_streams

    def compare(native: bool):
        monkeypatch.setenv("ADA_SIN_NATIVE", "1" if native else "0")
        with SinFieldStream.open(assembly) as base:
            if other == "itself":
                c = compare_result_streams(base)
            else:
                with SinFieldStream.open(perturbed) as o:
                    c = compare_result_streams(base, o)
        raw = [
            (d.max_abs_base.tobytes(), d.max_abs_other.tobytes(), d.max_abs_diff.tobytes(), d.max_diff_at)
            for d in c.diffs
        ]
        return _comparison_digest(c), raw, repr(c)

    native, numpy_path = compare(True), compare(False)
    assert native == numpy_path
    if other == "perturbed":
        assert any(d[2] != np.zeros(len(d[2]) // 8).tobytes() for d in native[1]), "the perturbation must show"


@pytest.mark.parametrize(
    "records,extra_pointers",
    [
        ([[1, 10, 0.5, 1.5], [2, 10, 2.5, 3.5], [1, 11, 4.5, 5.5]], []),
        ([[1, 10, 0.5], [2, 10, 2.5, 3.5, 4.5], [1, 11, 5.5], [2, 12, 6.5, 7.5, 8.5]], []),
        ([[1, 10, 0.5], [1, 10, 2.5, 3.5], [2, 11, 5.5]], [0, 10_000]),
        ([[3]], []),
    ],
)
def test_case_gathers_and_records_are_identical(records, extra_pointers, monkeypatch):
    from types import SimpleNamespace

    from ada.fem.formats.sesam.results.sin_reader import SinFile

    def read(native: bool):
        monkeypatch.setenv("ADA_SIN_NATIVE", "1" if native else "0")
        source, pointers = _word_file(records)
        pointers = np.concatenate((pointers, np.asarray(extra_pointers, dtype=np.int64)))
        block = SimpleNamespace(pointer_table=pointers)
        out = []
        for index in (False, True):
            sf = SimpleNamespace(source=source, type_blocks={"RVX": block}, index_cases=index)
            for word in (1, 2, 3, 99):
                g = SinFile.gather_records(sf, "RVX", where_first_word=word)
                out.append(None if g is None else (g.dtype.str, g.shape, g.tobytes()))
                n, w = SinFile.gather_ragged_records(sf, "RVX", where_first_word=word)
                out.append((n.dtype.str, n.tobytes(), w.dtype.str, w.tobytes()))
            block.__dict__.pop("_case_index", None)
        out.append(list(SinFile.iter_records(sf, "RVX")))
        out.append(list(SinFile.iter_records(sf, "RVX", where_first_word=1, where_second_word={10})))
        return out

    assert read(True) == read(False)


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
def test_derived_shell_values_are_identical(dtype, monkeypatch):
    from ada.fem.formats.sesam.results import derived_values as dv

    rng = np.random.default_rng(2)
    bottom = (rng.normal(size=(50, 4, 3)) * 1e7).astype(dtype)
    top = (rng.normal(size=(50, 4, 3)) * 1e7).astype(dtype)
    basic = np.concatenate((top, bottom), axis=1)
    thickness = rng.uniform(0.01, 0.03, size=50)

    def run(native: bool):
        monkeypatch.setenv("ADA_SIN_NATIVE", "1" if native else "0")
        d = dv.decompose_shell(bottom[:, :, :], top)
        return [
            a.tobytes()
            for a in (
                dv.general_stress(basic),
                dv.principal_of(basic, 0, 1, 2),
                dv.plane_principal(basic[..., 0], basic[..., 1], basic[..., 2]),
                d,
                dv.membrane_principal(d),
                dv.stress_resultants(d, thickness[:, None]),
                dv.stress_resultants(d, 0.02),
            )
        ]

    assert run(True) == run(False)


def test_femset_ids_fast_path_keeps_the_ids():
    from ada.fem import FemSet

    ids = list(range(1, 50_001))
    assert FemSet("s", ids, set_type="elset")._member_ids == ids
    mixed = [1, np.int64(2), True]
    assert FemSet("m", mixed, set_type="nset")._member_ids == [1, 2, 1]
    with pytest.raises(ValueError):
        FemSet("x", [1, 2], set_type=None)
