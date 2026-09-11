from typing import Optional

import flatbuffers
from ada.comms.fb.fb_base_serializer import serialize_error, serialize_fileobject
from ada.comms.fb.fb_server_gen import (
    ProceduralModelListEntryDC,
    ProceduralModelListReplyDC,
    ProceduralModelLoadDC,
    ProceduralModelLoadReplyDC,
    ProceduralModelSaveDC,
    ProceduralModelSaveReplyDC,
    ServerDC,
    ServerProcessInfoDC,
    ServerReplyDC,
)
from ada.comms.fb.server import (
    ProceduralModelListEntry,
    ProceduralModelListReply,
    ProceduralModelLoad,
    ProceduralModelLoadReply,
    ProceduralModelSave,
    ProceduralModelSaveReply,
    Server,
    ServerProcessInfo,
    ServerReply,
)


def serialize_serverprocessinfo(builder: flatbuffers.Builder, obj: Optional[ServerProcessInfoDC]) -> Optional[int]:
    if obj is None:
        return None
    log_file_path_str = None
    if obj.log_file_path is not None:
        log_file_path_str = builder.CreateString(str(obj.log_file_path))

    ServerProcessInfo.Start(builder)
    if obj.pid is not None:
        ServerProcessInfo.AddPid(builder, obj.pid)
    if obj.thread_id is not None:
        ServerProcessInfo.AddThreadId(builder, obj.thread_id)
    if log_file_path_str is not None:
        ServerProcessInfo.AddLogFilePath(builder, log_file_path_str)
    return ServerProcessInfo.End(builder)


def serialize_proceduralmodelsave(builder: flatbuffers.Builder, obj: Optional[ProceduralModelSaveDC]) -> Optional[int]:
    if obj is None:
        return None
    model_id_str = None
    if obj.model_id is not None:
        model_id_str = builder.CreateString(str(obj.model_id))
    doc_json_str = None
    if obj.doc_json is not None:
        doc_json_str = builder.CreateString(str(obj.doc_json))
    expected_content_hash_str = None
    if obj.expected_content_hash is not None:
        expected_content_hash_str = builder.CreateString(str(obj.expected_content_hash))

    ProceduralModelSave.Start(builder)
    if model_id_str is not None:
        ProceduralModelSave.AddModelId(builder, model_id_str)
    if doc_json_str is not None:
        ProceduralModelSave.AddDocJson(builder, doc_json_str)
    if expected_content_hash_str is not None:
        ProceduralModelSave.AddExpectedContentHash(builder, expected_content_hash_str)
    return ProceduralModelSave.End(builder)


def serialize_proceduralmodelsavereply(
    builder: flatbuffers.Builder, obj: Optional[ProceduralModelSaveReplyDC]
) -> Optional[int]:
    if obj is None:
        return None
    model_id_str = None
    if obj.model_id is not None:
        model_id_str = builder.CreateString(str(obj.model_id))
    content_hash_str = None
    if obj.content_hash is not None:
        content_hash_str = builder.CreateString(str(obj.content_hash))

    ProceduralModelSaveReply.Start(builder)
    if model_id_str is not None:
        ProceduralModelSaveReply.AddModelId(builder, model_id_str)
    if content_hash_str is not None:
        ProceduralModelSaveReply.AddContentHash(builder, content_hash_str)
    return ProceduralModelSaveReply.End(builder)


def serialize_proceduralmodellistentry(
    builder: flatbuffers.Builder, obj: Optional[ProceduralModelListEntryDC]
) -> Optional[int]:
    if obj is None:
        return None
    model_id_str = None
    if obj.model_id is not None:
        model_id_str = builder.CreateString(str(obj.model_id))
    content_hash_str = None
    if obj.content_hash is not None:
        content_hash_str = builder.CreateString(str(obj.content_hash))

    ProceduralModelListEntry.Start(builder)
    if model_id_str is not None:
        ProceduralModelListEntry.AddModelId(builder, model_id_str)
    if content_hash_str is not None:
        ProceduralModelListEntry.AddContentHash(builder, content_hash_str)
    if obj.modified_at is not None:
        ProceduralModelListEntry.AddModifiedAt(builder, obj.modified_at)
    if obj.size_bytes is not None:
        ProceduralModelListEntry.AddSizeBytes(builder, obj.size_bytes)
    return ProceduralModelListEntry.End(builder)


def serialize_proceduralmodellistreply(
    builder: flatbuffers.Builder, obj: Optional[ProceduralModelListReplyDC]
) -> Optional[int]:
    if obj is None:
        return None
    entries_vector = None
    if obj.entries is not None and len(obj.entries) > 0:
        entries_list = [serialize_proceduralmodellistentry(builder, item) for item in obj.entries]
        ProceduralModelListReply.StartEntriesVector(builder, len(entries_list))
        for item in reversed(entries_list):
            builder.PrependUOffsetTRelative(item)
        entries_vector = builder.EndVector()

    ProceduralModelListReply.Start(builder)
    if obj.entries is not None and len(obj.entries) > 0:
        ProceduralModelListReply.AddEntries(builder, entries_vector)
    return ProceduralModelListReply.End(builder)


def serialize_proceduralmodelload(builder: flatbuffers.Builder, obj: Optional[ProceduralModelLoadDC]) -> Optional[int]:
    if obj is None:
        return None
    model_id_str = None
    if obj.model_id is not None:
        model_id_str = builder.CreateString(str(obj.model_id))

    ProceduralModelLoad.Start(builder)
    if model_id_str is not None:
        ProceduralModelLoad.AddModelId(builder, model_id_str)
    return ProceduralModelLoad.End(builder)


def serialize_proceduralmodelloadreply(
    builder: flatbuffers.Builder, obj: Optional[ProceduralModelLoadReplyDC]
) -> Optional[int]:
    if obj is None:
        return None
    model_id_str = None
    if obj.model_id is not None:
        model_id_str = builder.CreateString(str(obj.model_id))
    doc_json_str = None
    if obj.doc_json is not None:
        doc_json_str = builder.CreateString(str(obj.doc_json))
    content_hash_str = None
    if obj.content_hash is not None:
        content_hash_str = builder.CreateString(str(obj.content_hash))

    ProceduralModelLoadReply.Start(builder)
    if model_id_str is not None:
        ProceduralModelLoadReply.AddModelId(builder, model_id_str)
    if doc_json_str is not None:
        ProceduralModelLoadReply.AddDocJson(builder, doc_json_str)
    if content_hash_str is not None:
        ProceduralModelLoadReply.AddContentHash(builder, content_hash_str)
    return ProceduralModelLoadReply.End(builder)


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
    process_info_obj = None
    if obj.process_info is not None:
        process_info_obj = serialize_serverprocessinfo(builder, obj.process_info)
    save_procedural_model_obj = None
    if obj.save_procedural_model is not None:
        save_procedural_model_obj = serialize_proceduralmodelsavereply(builder, obj.save_procedural_model)
    list_procedural_models_obj = None
    if obj.list_procedural_models is not None:
        list_procedural_models_obj = serialize_proceduralmodellistreply(builder, obj.list_procedural_models)
    load_procedural_model_obj = None
    if obj.load_procedural_model is not None:
        load_procedural_model_obj = serialize_proceduralmodelloadreply(builder, obj.load_procedural_model)

    ServerReply.Start(builder)
    if message_str is not None:
        ServerReply.AddMessage(builder, message_str)
    if obj.file_objects is not None and len(obj.file_objects) > 0:
        ServerReply.AddFileObjects(builder, file_objects_vector)
    if obj.reply_to is not None:
        ServerReply.AddReplyTo(builder, obj.reply_to.value)
    if obj.error is not None:
        ServerReply.AddError(builder, error_obj)
    if obj.process_info is not None:
        ServerReply.AddProcessInfo(builder, process_info_obj)
    if obj.save_procedural_model is not None:
        ServerReply.AddSaveProceduralModel(builder, save_procedural_model_obj)
    if obj.list_procedural_models is not None:
        ServerReply.AddListProceduralModels(builder, list_procedural_models_obj)
    if obj.load_procedural_model is not None:
        ServerReply.AddLoadProceduralModel(builder, load_procedural_model_obj)
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
    save_procedural_model_obj = None
    if obj.save_procedural_model is not None:
        save_procedural_model_obj = serialize_proceduralmodelsave(builder, obj.save_procedural_model)
    load_procedural_model_obj = None
    if obj.load_procedural_model is not None:
        load_procedural_model_obj = serialize_proceduralmodelload(builder, obj.load_procedural_model)

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
    if obj.save_procedural_model is not None:
        Server.AddSaveProceduralModel(builder, save_procedural_model_obj)
    if obj.load_procedural_model is not None:
        Server.AddLoadProceduralModel(builder, load_procedural_model_obj)
    return Server.End(builder)
