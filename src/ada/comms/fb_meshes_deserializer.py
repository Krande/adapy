from ada.comms.fb_meshes_gen import AppendMeshDC, MeshDC


def deserialize_mesh(fb_obj) -> MeshDC | None:
    if fb_obj is None:
        return None

    return MeshDC(
        indices=[fb_obj.Indices(i) for i in range(fb_obj.IndicesLength())] if fb_obj.IndicesLength() > 0 else None,
        vertices=[fb_obj.Vertices(i) for i in range(fb_obj.VerticesLength())] if fb_obj.VerticesLength() > 0 else None,
    )


def deserialize_appendmesh(fb_obj) -> AppendMeshDC | None:
    if fb_obj is None:
        return None

    return AppendMeshDC(mesh=deserialize_mesh(fb_obj.Mesh()))
