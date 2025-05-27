from typing import Optional

import flatbuffers
from ada.comms.fb.fb_base_serializer import serialize_error, serialize_fileobject
from ada.comms.fb.fb_server_gen import ServerDC, ServerReplyDC
from ada.comms.fb.server import Server, ServerReply


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
    error_obj = None
    if obj.error is not None:
        error_obj = serialize_error(builder, obj.error)

    ServerReply.Start(builder)
    if message_str is not None:
        ServerReply.AddMessage(builder, message_str)
    if obj.file_objects is not None and len(obj.file_objects) > 0:
        ServerReply.AddFileObjects(builder, file_objects_vector)
    if obj.error is not None:
        ServerReply.AddError(builder, error_obj)
    return ServerReply.End(builder)


def serialize_server(builder: flatbuffers.Builder, obj: Optional[ServerDC]) -> Optional[int]:
    if obj is None:
        return None
    new_file_object_obj = None
    if obj.new_file_object is not None:
        new_file_object_obj = serialize_fileobject(builder, obj.new_file_object)
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
    delete_file_object_obj = None
    if obj.delete_file_object is not None:
        delete_file_object_obj = serialize_fileobject(builder, obj.delete_file_object)
    start_file_in_local_app_obj = None
    if obj.start_file_in_local_app is not None:
        start_file_in_local_app_obj = serialize_fileobject(builder, obj.start_file_in_local_app)

    Server.Start(builder)
    if obj.new_file_object is not None:
        Server.AddNewFileObject(builder, new_file_object_obj)
    if obj.all_file_objects is not None and len(obj.all_file_objects) > 0:
        Server.AddAllFileObjects(builder, all_file_objects_vector)
    if get_file_object_by_name_str is not None:
        Server.AddGetFileObjectByName(builder, get_file_object_by_name_str)
    if get_file_object_by_path_str is not None:
        Server.AddGetFileObjectByPath(builder, get_file_object_by_path_str)
    if obj.delete_file_object is not None:
        Server.AddDeleteFileObject(builder, delete_file_object_obj)
    if obj.start_file_in_local_app is not None:
        Server.AddStartFileInLocalApp(builder, start_file_in_local_app_obj)
    return Server.End(builder)
