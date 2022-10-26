import numpy as np
import pytest

from ada.calc.ec3_ex import ec3_654
from ada.fem.formats.sesam.results.read_cc import read_cc_file
from ada.fem.formats.sesam.results.read_sif import read_sif_file


def test_read_static_line_results(cantilever_dir):
    results = read_sif_file(cantilever_dir / "sesam/static/line/STATIC_LINE_CANTILEVER_SESAMR1.SIF")
    assert len(results.results) == 2
    data = results.get_field_value_by_name("FORCES")
    _ = np.unique(data.values[:, 0])
    ec3_654()
    # results.to_fem_file("temp/sesam_line.vtu")
    # results.to_gltf("temp/sesam.glb", 1, "RVNODDIS", warp_field="RVNODDIS", warp_step=1, warp_scale=10)


def test_read_static_shell_results(cantilever_dir):
    results = read_sif_file(cantilever_dir / "sesam/static/shell/STATIC_SHELL_CANTILEVER_SESAMR1.SIF")
    assert len(results.results) == 2

    # results.to_fem_file("temp/sesam_shell.vtu")
    # results.to_gltf("temp/sesam.glb", 1, "RVNODDIS", warp_field="RVNODDIS", warp_step=1, warp_scale=10)


def test_ec3_code_check_results(cantilever_dir):
    results = read_cc_file(cantilever_dir / "sesam/static/line/Eurocode31.h5")

    cc_my_beam = results["MyBeam"]
    max_uf, cc_type, layer = cc_my_beam.get_max_utilization()

    assert cc_type == "uf654"
    assert max_uf == pytest.approx(0.68462473, abs=1e-5)
    assert layer == 0

    max_uf, cc_type, layer = cc_my_beam.get_max_utilization("uf662my")
    assert cc_type == "uf662my"
    assert max_uf == pytest.approx(0.3568629, abs=1e-5)
    assert layer == 0
