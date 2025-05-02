import flatbuffers
from typing import Optional

from ada.comms.server import ServerReply, Server

from ada.comms.fb_server_gen import ServerReplyDC, ServerDC

from ada.comms.fb_base_serializer import serialize_fileobject, serialize_error, serialize_fileobject, serialize_fileobject, serialize_fileobject, serialize_fileobject

def serialize_serverreply(builder: flatbuffers.Builder, obj: Optional[ServerReplyDC]) -> Optional[int]:
    if obj is None:
        return None
    message_str = None
    if obj.message is not None:
        message_str = builder.CreateString(str(obj.message))
    file_objects_vector = None
    if obj.file_objects is not None and len(obj.file_objects) > 0:
        file_objects_list = [serialize_fileobject(builder, item) for item in obj.file_objects]
        ServerReply.StartFileObjectsVector(builder, len(file_objects_list))
        for item in reversed(file_objects_list):
            builder.PrependUOffsetTRelative(item)
        file_objects_vector = builder.EndVector()

    ServerReply.Start(builder)
    if message_str is not None:
        ServerReply.AddMessage(builder, message_str)
    if obj.file_objects is not None and len(obj.file_objects) > 0:
        ServerReply.AddFileObjects(builder, file_objects_vector)
    return ServerReply.End(builder)


def serialize_server(builder: flatbuffers.Builder, obj: Optional[ServerDC]) -> Optional[int]:
    if obj is None:
        return None
    all_file_objects_vector = None
    if obj.all_file_objects is not None and len(obj.all_file_objects) > 0:
        all_file_objects_list = [serialize_fileobject(builder, item) for item in obj.all_file_objects]
        Server.StartAllFileObjectsVector(builder, len(all_file_objects_list))
        for item in reversed(all_file_objects_list):
            builder.PrependUOffsetTRelative(item)
        all_file_objects_vector = builder.EndVector()
    get_file_object_by_name_str = None
    if obj.get_file_object_by_name is not None:
        get_file_object_by_name_str = builder.CreateString(str(obj.get_file_object_by_name))
    get_file_object_by_path_str = None
    if obj.get_file_object_by_path is not None:
        get_file_object_by_path_str = builder.CreateString(str(obj.get_file_object_by_path))

    Server.Start(builder)
    if obj.all_file_objects is not None and len(obj.all_file_objects) > 0:
        Server.AddAllFileObjects(builder, all_file_objects_vector)
    if get_file_object_by_name_str is not None:
        Server.AddGetFileObjectByName(builder, get_file_object_by_name_str)
    if get_file_object_by_path_str is not None:
        Server.AddGetFileObjectByPath(builder, get_file_object_by_path_str)
    return Server.End(builder)


