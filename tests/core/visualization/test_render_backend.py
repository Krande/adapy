from ada.visit.render_backend import SqLiteBackend


def test_sqlite_backend(example_files):
    backend = SqLiteBackend()
    scene = backend.add_glb(example_files / "gltf_files" / "boxes_merged.glb")
    assert len(scene.geometry) == 12

    mesh_data = backend.get_mesh_data_from_face_index(120, 8, "boxes_merged")
    assert mesh_data.mesh_id == "3cYJcny$CHxQS0w2f4ZOUQ"
