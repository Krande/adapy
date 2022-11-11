import pytest

from ada.fem.formats.calculix.results.read_frd_file import read_from_frd_file_proto, ReadFrdFailedException


def test_read_static_line_calculix_results(cantilever_dir):
    with pytest.raises(ReadFrdFailedException):
        _ = read_from_frd_file_proto(cantilever_dir / "calculix/static_line_cantilever_calculix.frd")


def test_read_static_shell_calculix_results(cantilever_dir):
    res = read_from_frd_file_proto(cantilever_dir / "calculix/static_shell_cantilever_calculix.frd")
    assert len(res.results) == 8

    # res.to_gltf("temp/ccx_model_sh.glb", 2, "DISP", warp_field="DISP", warp_step=2, warp_scale=10)


def test_read_static_solid_calculix_results(cantilever_dir):
    res = read_from_frd_file_proto(cantilever_dir / "calculix/static_solid_cantilever_calculix.frd")
    assert len(res.results) == 8

    # res.to_gltf("temp/ccx_model_so.glb", 2, "DISP", warp_field="DISP", warp_step=2, warp_scale=10)
