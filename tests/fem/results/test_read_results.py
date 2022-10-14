import ada


def test_read_static_cantilever(fem_files):
    res = ada.from_fem_res(fem_files / "calculix/static_cantilever_calculix.frd", proto_reader=False)
    vm = res.to_vis_mesh()
    vm.to_gltf("temp/model.glb")
    print("ds")
