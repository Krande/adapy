from ada.comms.meshes import AppendMesh

from ada.comms.fb_meshes_gen import MeshDC, AppendMeshDC

def deserialize_mesh(fb_obj) -> MeshDC | None:
    if fb_obj is None:
        return None

    return MeshDC(
        indices=[fb_obj.Indices(i) for i in range(fb_obj.IndicesLength())] if fb_obj.IndicesLength() > 0 else None,
        vertices=[fb_obj.Vertices(i) for i in range(fb_obj.VerticesLength())] if fb_obj.VerticesLength() > 0 else None
    )


def deserialize_appendmesh(fb_obj) -> AppendMeshDC | None:
    if fb_obj is None:
        return None

    return AppendMeshDC(
        mesh=deserialize_mesh(fb_obj.Mesh())
    )


def deserialize_root_appendmesh(bytes_obj: bytes) -> AppendMeshDC:
    fb_obj = AppendMesh.AppendMesh.GetRootAsAppendMesh(bytes_obj, 0)
    return deserialize_appendmesh(fb_obj)
