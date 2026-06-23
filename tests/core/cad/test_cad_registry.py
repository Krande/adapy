"""The CAD backend + tessellation-path registry and CadConfig.

Guards the ergonomic discovery/selection API: when adacpp is installed its extra tessellation
paths are listed, CadConfig.default() prefers libtess2, the path enum exposes backend/pipeline,
and the config attaches to an Assembly.
"""

from __future__ import annotations

import pytest


def test_registry_lists_adacpp_paths_when_installed():
    pytest.importorskip("adacpp")
    from ada.cad import CadBackendName, TessellationPath, available_backends, available_paths

    assert CadBackendName.ADACPP in available_backends()
    paths = available_paths()
    # adacpp present => all its paths are listed
    for p in (
        TessellationPath.ADACPP_LIBTESS2,
        TessellationPath.ADACPP_OCC,
        TessellationPath.ADACPP_CGAL,
        TessellationPath.ADACPP_HYBRID,
    ):
        assert p in paths


def test_tessellation_path_enum_backend_and_pipeline():
    from ada.cad import CadBackendName, TessellationPath

    assert TessellationPath.OCC.backend is CadBackendName.OCC
    assert TessellationPath.OCC.pipeline is None  # OCC BRepMesh, no adacpp pipeline
    assert TessellationPath.ADACPP_LIBTESS2.backend is CadBackendName.ADACPP
    assert TessellationPath.ADACPP_LIBTESS2.pipeline == "libtess2"
    assert TessellationPath.ADACPP_HYBRID.pipeline == "hybrid"


def test_cadconfig_default_prefers_libtess2_when_adacpp_installed():
    pytest.importorskip("adacpp")
    from ada.cad import CadConfig, TessellationPath

    cfg = CadConfig.default()
    assert cfg.path == TessellationPath.ADACPP_LIBTESS2
    cfg.validate()  # the default must be available
    env = cfg.env()
    assert env["ADAPY_CAD_BACKEND"] == "adacpp"
    assert env["ADA_STREAM_TESS_PIPELINE"] == "libtess2"

    # simplify maps to the cleanup env var
    assert CadConfig(path=TessellationPath.ADACPP_LIBTESS2, simplify=True).env()["ADA_STREAM_SIMPLIFY"] == "1"


def test_cadconfig_validate_rejects_unavailable_path():
    from ada.cad import CadConfig, TessellationPath, available_paths

    missing = next((p for p in TessellationPath if p not in available_paths()), None)
    if missing is None:
        pytest.skip("every path is available in this environment")
    with pytest.raises(ValueError):
        CadConfig(path=missing).validate()


def test_assembly_cad_config_attaches():
    import ada
    from ada.cad import CadConfig, TessellationPath, available_paths

    a = ada.Assembly("a")
    assert a.cad_config.path in available_paths()  # lazy default is something usable
    target = next(p for p in available_paths())
    a.cad_config = CadConfig(path=target, deflection=1.0)
    assert a.cad_config.path == target and a.cad_config.deflection == 1.0
