"""Field values in a memory-mapped file: the same result as one held in memory."""

from __future__ import annotations

import gc
import hashlib
import mmap
import pathlib
import weakref
from types import SimpleNamespace

import numpy as np
import pytest

from ada.fem.results.field_cache import FieldCache

from ..formats.sesam.sin_assembly import SIN_PATH, write_assembly_sin

RMED_PATH = pathlib.Path(__file__).resolve().parents[4] / (
    "files/fem_files/cantilever/code_aster/static_shell_cantilever_code_aster.rmed"
)


@pytest.fixture(scope="module")
def assembly(tmp_path_factory):
    return write_assembly_sin(tmp_path_factory.mktemp("cache") / "ASSEMBLY.SIN")


def _fields(*arrays):
    return [SimpleNamespace(values=a) for a in arrays]


def _mapped(values) -> bool:
    return isinstance(values.base, mmap.mmap)


def test_spilled_values_keep_bytes_dtype_shape_and_order(tmp_path):
    rng = np.random.default_rng(3)
    arrays = [
        rng.normal(size=(40, 7)),
        np.asfortranarray(rng.integers(0, 9, size=(5, 3)).astype(np.int32)),
        rng.normal(size=(6, 4))[:, ::2],
        (rng.normal(size=9) + 1j).astype(np.complex64),
        rng.normal(size=(3, 2)).astype(np.float32),
    ]
    fields = _fields(*(a.copy(order="K") for a in arrays))
    with FieldCache(tmp_path) as cache:
        cache.spill(fields[:2])
        cache.spill(fields[2:])
    for f, a in zip(fields, arrays):
        assert _mapped(f.values)
        assert f.values.dtype == a.dtype and f.values.shape == a.shape
        assert np.array_equal(f.values, a)
    assert fields[1].values.flags.f_contiguous and not fields[1].values.flags.c_contiguous


def test_what_cannot_be_mapped_stays_and_a_shared_array_is_written_once(tmp_path):
    shared = np.arange(10.0)
    empty, objects = np.empty((0, 3)), np.array([None, 1], dtype=object)
    fields = _fields(shared, shared, empty, objects)
    with FieldCache(tmp_path) as cache:
        cache.spill(fields)
    assert fields[0].values is fields[1].values and _mapped(fields[0].values)
    assert fields[2].values is empty and fields[3].values is objects
    assert cache.nbytes == shared.nbytes


def test_writing_to_a_value_changes_the_copy_not_the_file(tmp_path):
    fields = _fields(np.zeros(4), np.ones(4))
    with FieldCache(tmp_path) as cache:
        cache.spill(fields)
        fields[0].values[:] = 7.0
        later = _fields(np.full(4, 2.0))
        cache.spill(later)
    assert np.array_equal(fields[0].values, np.full(4, 7.0))
    assert np.array_equal(fields[1].values, np.ones(4))
    assert np.array_equal(later[0].values, np.full(4, 2.0))


def test_the_file_goes_with_the_last_value(tmp_path):
    fields = _fields(np.arange(5.0))
    cache = FieldCache(tmp_path)
    cache.spill(fields)
    cache.close()
    held = weakref.ref(fields[0].values.base)
    assert np.array_equal(fields[0].values, np.arange(5.0))
    del fields
    gc.collect()
    assert held() is None
    assert not cache.path.exists()


def _ordered_digest(result) -> list:
    return [
        (
            type(f).__name__,
            f.name,
            int(f.step),
            str(getattr(f, "elem_type", None)),
            str(getattr(f, "field_pos", None)),
            str(f.values.dtype),
            f.values.shape,
            f.values.flags.f_contiguous and not f.values.flags.c_contiguous,
            hashlib.sha256(np.ascontiguousarray(f.values).tobytes()).hexdigest(),
            repr(f.presentation),
            list(f.components or []),
            f.eigen_freq,
            f.eigen_value,
        )
        for f in result.results
    ]


def _mesh_digest(mesh) -> str:
    h = hashlib.sha256()
    for name in ("coords", "identifiers", "labels"):
        a = getattr(mesh.nodes, name, None)
        if a is not None:
            h.update(np.ascontiguousarray(a).tobytes())
    for block in mesh.elements:
        for a in (block.node_refs, block.identifiers, block.labels):
            if a is not None:
                h.update(np.ascontiguousarray(a).tobytes())
        h.update(repr(block.elem_info).encode())
    return h.hexdigest()


def _metadata(result) -> dict:
    out = {k: repr(v) for k, v in vars(result).items() if k not in ("results", "mesh")}
    out["mesh"] = _mesh_digest(result.mesh)
    return out


@pytest.mark.parametrize(
    "which,kw",
    [
        ("single", {}),
        ("single", {"step": 1}),
        ("assembly", {}),
        ("assembly", {"super_element": "SEL11.IND1"}),
        ("assembly", {"super_element": ["SEL10.IND1", "SEL11.IND1"]}),
        ("assembly", {"complex_phase": 0.5}),
    ],
)
def test_a_cached_sin_read_is_the_full_read(which, kw, assembly, tmp_path):
    from ada.fem.formats.sesam.results.read_sin import read_sin_file

    path = SIN_PATH if which == "single" else assembly
    full = read_sin_file(path, **kw)
    cached = read_sin_file(path, field_cache=tmp_path, **kw)
    assert full.results, "nothing read"
    assert _ordered_digest(cached) == _ordered_digest(full)
    assert _metadata(cached) == _metadata(full)
    assert all(_mapped(f.values) for f in cached.results if f.values.size)
    assert all(f._mesh is cached.mesh for f in cached.results)


@pytest.mark.parametrize("which", ["single", "assembly"])
def test_every_sin_read_lists_the_fields_in_one_order(which, assembly):
    from ada.fem.formats.sesam.results.read_sin import in_field_order, read_sin_file
    from ada.fem.formats.sesam.results.sin_field_stream import SinFieldStream

    path = SIN_PATH if which == "single" else assembly
    full = read_sin_file(path).results
    keys = [
        (type(f).__name__, f.name, int(f.step), str(f.elem_type if hasattr(f, "elem_type") else None)) for f in full
    ]
    keys = [k + (str(getattr(f, "field_pos", None)),) for k, f in zip(keys, full)]
    assert len(set(keys)) == len(keys), "the order key must tell every field apart"
    assert in_field_order(full) == full
    with SinFieldStream.open(path) as stream:
        streamed = [f for _, chunk in stream.iter_chunks() for f in chunk]
    assert [f.name for f in in_field_order(streamed)] == [f.name for f in full]


def test_from_fem_res_takes_the_cache_from_the_argument_or_the_environment(monkeypatch, tmp_path):
    import ada

    plain = ada.from_fem_res(SIN_PATH)
    assert not any(_mapped(f.values) for f in plain.results)
    by_arg = ada.from_fem_res(SIN_PATH, field_cache=tmp_path)
    monkeypatch.setenv("ADA_FEM_RES_CACHE", "1")
    monkeypatch.setenv("ADA_FIELD_CACHE_DIR", str(tmp_path))
    by_env = ada.from_fem_res(SIN_PATH)
    for result in (by_arg, by_env):
        assert _ordered_digest(result) == _ordered_digest(plain)
        assert all(_mapped(f.values) for f in result.results if f.values.size)
    assert not any(_mapped(f.values) for f in ada.from_fem_res(SIN_PATH, field_cache=False).results)


def test_another_format_is_read_then_moved_to_the_file(tmp_path):
    import ada

    plain = ada.from_fem_res(RMED_PATH)
    cached = ada.from_fem_res(RMED_PATH, field_cache=tmp_path)
    assert plain.results
    assert _ordered_digest(cached) == _ordered_digest(plain)
    assert all(_mapped(f.values) for f in cached.results if f.values.size)


def _bake_digests(out: pathlib.Path) -> dict:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir()) if p.is_file()}


@pytest.mark.parametrize("which", ["single", "assembly"])
def test_a_cached_bake_is_the_materialised_bake(which, assembly, monkeypatch, tmp_path):
    from ada.fem.results.artefacts import bake_fea_artefacts_from_source

    path = SIN_PATH if which == "single" else assembly
    monkeypatch.setenv("ADA_FEA_SIN_STREAMER", "off")
    monkeypatch.setenv("ADA_FIELD_CACHE_DIR", str(tmp_path))
    bake_fea_artefacts_from_source(path, tmp_path / "memory", src_key="m")
    monkeypatch.setenv("ADA_FEA_SIN_FIELD_CACHE", "1")
    bake_fea_artefacts_from_source(path, tmp_path / "cached", src_key="m")
    memory, cached = _bake_digests(tmp_path / "memory"), _bake_digests(tmp_path / "cached")
    assert memory and memory == cached


def test_with_the_cache_a_deck_too_big_to_materialise_is_read_whole(assembly, monkeypatch, tmp_path):
    from ada.fem.formats.sesam.results.read_sin import SinStreamReader
    from ada.fem.results.artefacts import (
        FEAResultStreamAdapter,
        make_stream_reader,
        readers,
    )

    monkeypatch.delenv("ADA_FEA_SIN_STREAMER", raising=False)
    monkeypatch.setenv("ADA_FIELD_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(readers, "SIN_MATERIALISE_MAX_ROWS", 0)

    def opened(cache: str | None, streamer: str | None = None, steps=None):
        for env, value in (("ADA_FEA_SIN_FIELD_CACHE", cache), ("ADA_FEA_SIN_STREAMER", streamer)):
            if value is None:
                monkeypatch.delenv(env, raising=False)
            else:
                monkeypatch.setenv(env, value)
        return make_stream_reader(assembly, steps=steps)

    with opened(None) as reader:
        assert type(reader) is SinStreamReader
    with opened("1") as reader:
        assert type(reader) is FEAResultStreamAdapter
        result = reader._result
        assert all(_mapped(f.values) for f in result.results if f.values.size)
        assert len(reader.read_mesh_geometry().points) == len(result.mesh.nodes.identifiers)
    with opened("1", streamer="on") as reader:
        assert type(reader) is SinStreamReader
    with opened("1", steps=[1]) as reader:
        assert type(reader) is SinStreamReader
