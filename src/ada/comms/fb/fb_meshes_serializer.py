from typing import Optional

import flatbuffers
from ada.comms.fb.fb_meshes_gen import AppendMeshDC, MeshDC, MeshInfoDC
from ada.comms.fb.meshes import AppendMesh, Mesh, MeshInfo


def serialize_mesh(builder: flatbuffers.Builder, obj: Optional[MeshDC]) -> Optional[int]:
    if obj is None:
        return None
    name_str = None
    if obj.name is not None:
        name_str = builder.CreateString(str(obj.name))
    Mesh.StartIndicesVector(builder, len(obj.indices))
    for item in reversed(obj.indices):
        builder.PrependUint32(item)
    indices_vector = builder.EndVector()
    Mesh.StartVerticesVector(builder, len(obj.vertices))
    for item in reversed(obj.vertices):
        builder.PrependFloat32(item)
    vertices_vector = builder.EndVector()
    parent_name_str = None
    if obj.parent_name is not None:
        parent_name_str = builder.CreateString(str(obj.parent_name))

    Mesh.Start(builder)
    if name_str is not None:
        Mesh.AddName(builder, name_str)
    if indices_vector is not None:
        Mesh.AddIndices(builder, indices_vector)
    if obj.vertices is not None:
        Mesh.AddVertices(builder, vertices_vector)
    if parent_name_str is not None:
        Mesh.AddParentName(builder, parent_name_str)
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


def serialize_meshinfo(builder: flatbuffers.Builder, obj: Optional[MeshInfoDC]) -> Optional[int]:
    if obj is None:
        return None
    object_name_str = None
    if obj.object_name is not None:
        object_name_str = builder.CreateString(str(obj.object_name))
    json_data_str = None
    if obj.json_data is not None:
        json_data_str = builder.CreateString(str(obj.json_data))
    file_name_str = None
    if obj.file_name is not None:
        file_name_str = builder.CreateString(str(obj.file_name))

    MeshInfo.Start(builder)
    if object_name_str is not None:
        MeshInfo.AddObjectName(builder, object_name_str)
    if obj.face_index is not None:
        MeshInfo.AddFaceIndex(builder, obj.face_index)
    if json_data_str is not None:
        MeshInfo.AddJsonData(builder, json_data_str)
    if file_name_str is not None:
        MeshInfo.AddFileName(builder, file_name_str)
    return MeshInfo.End(builder)
