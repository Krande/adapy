import flatbuffers
from typing import Optional

from ada.comms.meshes import Mesh, AppendMesh

from ada.comms.fb_meshes_gen import MeshDC, AppendMeshDC


def serialize_mesh(builder: flatbuffers.Builder, obj: Optional[MeshDC]) -> Optional[int]:
    if obj is None:
        return None
    Mesh.StartIndicesVector(builder, len(obj.indices))
    for item in reversed(obj.indices):
        builder.PrependUint32(item)
    indices_vector = builder.EndVector(len(obj.indices))
    Mesh.StartVerticesVector(builder, len(obj.vertices))
    for item in reversed(obj.vertices):
        builder.PrependFloat32(item)
    vertices_vector = builder.EndVector(len(obj.vertices))

    Mesh.Start(builder)
    if indices_vector is not None:
        Mesh.AddIndices(builder, indices_vector)
    if obj.vertices is not None:
        Mesh.AddVertices(builder, vertices_vector)
    return Mesh.End(builder)


def serialize_appendmesh(builder: flatbuffers.Builder, obj: Optional[AppendMeshDC]) -> Optional[int]:
    if obj is None:
        return None
    mesh_obj = None
    if obj.mesh is not None:
        mesh_obj = serialize_mesh(builder, obj.mesh)

    AppendMesh.Start(builder)
    if obj.mesh is not None:
        AppendMesh.AddMesh(builder, mesh_obj)
    return AppendMesh.End(builder)


