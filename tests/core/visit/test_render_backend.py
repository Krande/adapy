from ada.visit.rendering.render_backend import SqLiteBackend


def test_sqlite_backend(example_files):
    backend = SqLiteBackend()
    scene = backend.glb_to_trimesh_scene(example_files / "gltf_files" / "boxes_merged.glb")

    assert len(scene.geometry) == 14

    backend.add_metadata(scene.metadata, "boxes_merged")
    backend.commit()

    mesh_data = backend.get_mesh_data_from_face_index(120, 8, "boxes_merged")
    assert mesh_data.mesh_id == "24"
