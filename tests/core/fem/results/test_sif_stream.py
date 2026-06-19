"""Streaming SIF bake reader (`SifStreamReader`) + its env gate.

The streaming reader keeps one step resident at a time (via the byte-offset
index) so a many-mode SIF can bake without materialising the whole result. It
must produce *exactly* what the default full-materialise adapter produces —
these tests pin that equivalence at the reader level (mesh, specs, every
per-step value) and end-to-end (the bake's field blobs are byte-identical with
the flag off vs on). The gate (`ADA_FEA_SIF_STREAMER`) defaults off.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pytest

from ada.fem.formats.sesam.results.read_sif import read_sif_file
from ada.fem.formats.sesam.results.sif_stream import SifStreamReader
from ada.fem.results.artefacts import (
    FEAResultStreamAdapter,
    bake_fea_artefacts_from_source,
    make_stream_reader,
)

_EIGEN = "cantilever/sesam/eigen/shell/EIGEN_SHELL_CANTILEVER_SESAMR1.SIF"  # 20 steps


def test_stream_reader_mesh_and_specs_match_adapter(fem_files):
    sif = fem_files / _EIGEN
    ref = FEAResultStreamAdapter(read_sif_file(sif))
    strm = SifStreamReader(sif)

    g1, g2 = ref.read_mesh_geometry(), strm.read_mesh_geometry()
    assert g1.points.shape == g2.points.shape
    assert np.allclose(g1.points, g2.points)

    assert {s.name for s in ref.field_specs()} == {s.name for s in strm.field_specs()}
    # The streamer advertises the global step count, not a single step's.
    for s in strm.field_specs():
        assert s.n_steps == 20
    assert {(s.name, s.elem_type) for s in ref.element_field_specs()} == {
        (s.name, s.elem_type) for s in strm.element_field_specs()
    }


def test_stream_reader_nodal_values_match_adapter(fem_files):
    sif = fem_files / _EIGEN
    ref = FEAResultStreamAdapter(read_sif_file(sif))
    strm = SifStreamReader(sif)
    for spec in strm.field_specs():
        if spec.support != "nodal":
            continue
        a = list(ref.iter_field_steps(spec.name))
        b = list(strm.iter_field_steps(spec.name))
        assert len(a) == len(b) == 20
        for x, y in zip(a, b):
            assert x.values.shape == y.values.shape
            assert np.allclose(x.values, y.values)


def test_stream_reader_element_values_match_adapter(fem_files):
    sif = fem_files / _EIGEN
    ref = FEAResultStreamAdapter(read_sif_file(sif))
    strm = SifStreamReader(sif)
    ref_specs = {(s.name, s.elem_type): s for s in ref.element_field_specs()}
    strm_specs = strm.element_field_specs()
    assert strm_specs  # the eigen shell deck has element STRESS
    for spec in strm_specs:
        rspec = ref_specs[(spec.name, spec.elem_type)]
        assert spec.element_labels == rspec.element_labels  # order is load-bearing
        a = list(ref.iter_element_field_steps(rspec))
        b = list(strm.iter_element_field_steps(spec))
        assert len(a) == len(b) == 20
        for x, y in zip(a, b):
            assert x.values.shape == y.values.shape
            assert np.allclose(x.values, y.values)


def test_make_sif_reader_gate_defaults_off(fem_files, monkeypatch):
    sif = fem_files / _EIGEN
    monkeypatch.delenv("ADA_FEA_SIF_STREAMER", raising=False)
    with make_stream_reader(sif) as r:
        assert isinstance(r, FEAResultStreamAdapter)

    monkeypatch.setenv("ADA_FEA_SIF_STREAMER", "1")
    with make_stream_reader(sif) as r:
        assert isinstance(r, SifStreamReader)


def test_bake_blobs_identical_streaming_vs_default(tmp_path, fem_files, monkeypatch):
    # End-to-end: the bake's field blobs must be byte-identical with the
    # streamer on vs off — the streamer only changes memory, not output.
    sif = fem_files / _EIGEN

    monkeypatch.delenv("ADA_FEA_SIF_STREAMER", raising=False)
    bake_def = bake_fea_artefacts_from_source(sif, tmp_path / "default", src_key=sif.stem)

    monkeypatch.setenv("ADA_FEA_SIF_STREAMER", "1")
    bake_strm = bake_fea_artefacts_from_source(sif, tmp_path / "stream", src_key=sif.stem)

    def blobs(d):
        return sorted(p.name for p in d.iterdir() if p.suffix == ".bin")

    names = blobs(bake_def.out_dir)
    assert names and names == blobs(bake_strm.out_dir)
    for name in names:
        a = (bake_def.out_dir / name).read_bytes()
        b = (bake_strm.out_dir / name).read_bytes()
        assert a == b, f"blob {name} differs between default and streaming bake"
