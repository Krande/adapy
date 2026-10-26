from typing import Optional

import flatbuffers
from ada.comms.fb.fb_base_serializer import serialize_error, serialize_fileobject
from ada.comms.fb.fb_server_gen import (
    FeaBlobReplyDC,
    FeaBlobRequestDC,
    FeaManifestReplyDC,
    FeaManifestRequestDC,
    FeaSourceInfoDC,
    FeaSourceRegisterDC,
    KernelCallDC,
    KernelReplyDC,
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
    FeaBlobReply,
    FeaBlobRequest,
    FeaManifestReply,
    FeaManifestRequest,
    FeaSourceInfo,
    FeaSourceRegister,
    KernelCall,
    KernelReply,
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


def serialize_feasourceregister(builder: flatbuffers.Builder, obj: Optional[FeaSourceRegisterDC]) -> Optional[int]:
    if obj is None:
        return None
    source_key_str = None
    if obj.source_key is not None:
        source_key_str = builder.CreateString(str(obj.source_key))
    bake_dir_str = None
    if obj.bake_dir is not None:
        bake_dir_str = builder.CreateString(str(obj.bake_dir))
    bake_id_str = None
    if obj.bake_id is not None:
        bake_id_str = builder.CreateString(str(obj.bake_id))

    FeaSourceRegister.Start(builder)
    if source_key_str is not None:
        FeaSourceRegister.AddSourceKey(builder, source_key_str)
    if bake_dir_str is not None:
        FeaSourceRegister.AddBakeDir(builder, bake_dir_str)
    if bake_id_str is not None:
        FeaSourceRegister.AddBakeId(builder, bake_id_str)
    if obj.open_in_viewer is not None:
        FeaSourceRegister.AddOpenInViewer(builder, obj.open_in_viewer)
    return FeaSourceRegister.End(builder)


def serialize_feasourceinfo(builder: flatbuffers.Builder, obj: Optional[FeaSourceInfoDC]) -> Optional[int]:
    if obj is None:
        return None
    source_key_str = None
    if obj.source_key is not None:
        source_key_str = builder.CreateString(str(obj.source_key))
    bake_id_str = None
    if obj.bake_id is not None:
        bake_id_str = builder.CreateString(str(obj.bake_id))

    FeaSourceInfo.Start(builder)
    if source_key_str is not None:
        FeaSourceInfo.AddSourceKey(builder, source_key_str)
    if bake_id_str is not None:
        FeaSourceInfo.AddBakeId(builder, bake_id_str)
    return FeaSourceInfo.End(builder)


def serialize_feamanifestrequest(builder: flatbuffers.Builder, obj: Optional[FeaManifestRequestDC]) -> Optional[int]:
    if obj is None:
        return None
    source_key_str = None
    if obj.source_key is not None:
        source_key_str = builder.CreateString(str(obj.source_key))

    FeaManifestRequest.Start(builder)
    if source_key_str is not None:
        FeaManifestRequest.AddSourceKey(builder, source_key_str)
    return FeaManifestRequest.End(builder)


def serialize_feamanifestreply(builder: flatbuffers.Builder, obj: Optional[FeaManifestReplyDC]) -> Optional[int]:
    if obj is None:
        return None
    source_key_str = None
    if obj.source_key is not None:
        source_key_str = builder.CreateString(str(obj.source_key))
    bake_id_str = None
    if obj.bake_id is not None:
        bake_id_str = builder.CreateString(str(obj.bake_id))
    manifest_json_str = None
    if obj.manifest_json is not None:
        manifest_json_str = builder.CreateString(str(obj.manifest_json))

    FeaManifestReply.Start(builder)
    if source_key_str is not None:
        FeaManifestReply.AddSourceKey(builder, source_key_str)
    if bake_id_str is not None:
        FeaManifestReply.AddBakeId(builder, bake_id_str)
    if manifest_json_str is not None:
        FeaManifestReply.AddManifestJson(builder, manifest_json_str)
    return FeaManifestReply.End(builder)


def serialize_feablobrequest(builder: flatbuffers.Builder, obj: Optional[FeaBlobRequestDC]) -> Optional[int]:
    if obj is None:
        return None
    source_key_str = None
    if obj.source_key is not None:
        source_key_str = builder.CreateString(str(obj.source_key))
    filename_str = None
    if obj.filename is not None:
        filename_str = builder.CreateString(str(obj.filename))

    FeaBlobRequest.Start(builder)
    if source_key_str is not None:
        FeaBlobRequest.AddSourceKey(builder, source_key_str)
    if filename_str is not None:
        FeaBlobRequest.AddFilename(builder, filename_str)
    if obj.ranged is not None:
        FeaBlobRequest.AddRanged(builder, obj.ranged)
    if obj.start is not None:
        FeaBlobRequest.AddStart(builder, obj.start)
    if obj.end is not None:
        FeaBlobRequest.AddEnd(builder, obj.end)
    return FeaBlobRequest.End(builder)


def serialize_feablobreply(builder: flatbuffers.Builder, obj: Optional[FeaBlobReplyDC]) -> Optional[int]:
    if obj is None:
        return None
    source_key_str = None
    if obj.source_key is not None:
        source_key_str = builder.CreateString(str(obj.source_key))
    filename_str = None
    if obj.filename is not None:
        filename_str = builder.CreateString(str(obj.filename))
    data_vector = None
    if obj.data is not None:
        data_vector = builder.CreateByteVector(obj.data)

    FeaBlobReply.Start(builder)
    if source_key_str is not None:
        FeaBlobReply.AddSourceKey(builder, source_key_str)
    if filename_str is not None:
        FeaBlobReply.AddFilename(builder, filename_str)
    if data_vector is not None:
        FeaBlobReply.AddData(builder, data_vector)
    if obj.ranged is not None:
        FeaBlobReply.AddRanged(builder, obj.ranged)
    if obj.total_size is not None:
        FeaBlobReply.AddTotalSize(builder, obj.total_size)
    return FeaBlobReply.End(builder)


def serialize_kernelcall(builder: flatbuffers.Builder, obj: Optional[KernelCallDC]) -> Optional[int]:
    if obj is None:
        return None
    function_str = None
    if obj.function is not None:
        function_str = builder.CreateString(str(obj.function))
    args_json_str = None
    if obj.args_json is not None:
        args_json_str = builder.CreateString(str(obj.args_json))
    data_vector = None
    if obj.data is not None:
        data_vector = builder.CreateByteVector(obj.data)

    KernelCall.Start(builder)
    if function_str is not None:
        KernelCall.AddFunction(builder, function_str)
    if args_json_str is not None:
        KernelCall.AddArgsJson(builder, args_json_str)
    if data_vector is not None:
        KernelCall.AddData(builder, data_vector)
    return KernelCall.End(builder)


def serialize_kernelreply(builder: flatbuffers.Builder, obj: Optional[KernelReplyDC]) -> Optional[int]:
    if obj is None:
        return None
    function_str = None
    if obj.function is not None:
        function_str = builder.CreateString(str(obj.function))
    result_json_str = None
    if obj.result_json is not None:
        result_json_str = builder.CreateString(str(obj.result_json))
    data_vector = None
    if obj.data is not None:
        data_vector = builder.CreateByteVector(obj.data)

    KernelReply.Start(builder)
    if function_str is not None:
        KernelReply.AddFunction(builder, function_str)
    if result_json_str is not None:
        KernelReply.AddResultJson(builder, result_json_str)
    if data_vector is not None:
        KernelReply.AddData(builder, data_vector)
    return KernelReply.End(builder)


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
    fea_source_obj = None
    if obj.fea_source is not None:
        fea_source_obj = serialize_feasourceinfo(builder, obj.fea_source)
    fea_manifest_obj = None
    if obj.fea_manifest is not None:
        fea_manifest_obj = serialize_feamanifestreply(builder, obj.fea_manifest)
    fea_blob_obj = None
    if obj.fea_blob is not None:
        fea_blob_obj = serialize_feablobreply(builder, obj.fea_blob)
    kernel_obj = None
    if obj.kernel is not None:
        kernel_obj = serialize_kernelreply(builder, obj.kernel)

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
    if obj.fea_source is not None:
        ServerReply.AddFeaSource(builder, fea_source_obj)
    if obj.fea_manifest is not None:
        ServerReply.AddFeaManifest(builder, fea_manifest_obj)
    if obj.fea_blob is not None:
        ServerReply.AddFeaBlob(builder, fea_blob_obj)
    if obj.kernel is not None:
        ServerReply.AddKernel(builder, kernel_obj)
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
    register_fea_source_obj = None
    if obj.register_fea_source is not None:
        register_fea_source_obj = serialize_feasourceregister(builder, obj.register_fea_source)
    open_fea_source_obj = None
    if obj.open_fea_source is not None:
        open_fea_source_obj = serialize_feasourceinfo(builder, obj.open_fea_source)
    fea_manifest_request_obj = None
    if obj.fea_manifest_request is not None:
        fea_manifest_request_obj = serialize_feamanifestrequest(builder, obj.fea_manifest_request)
    fea_blob_request_obj = None
    if obj.fea_blob_request is not None:
        fea_blob_request_obj = serialize_feablobrequest(builder, obj.fea_blob_request)
    kernel_call_obj = None
    if obj.kernel_call is not None:
        kernel_call_obj = serialize_kernelcall(builder, obj.kernel_call)

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
    if obj.register_fea_source is not None:
        Server.AddRegisterFeaSource(builder, register_fea_source_obj)
    if obj.open_fea_source is not None:
        Server.AddOpenFeaSource(builder, open_fea_source_obj)
    if obj.fea_manifest_request is not None:
        Server.AddFeaManifestRequest(builder, fea_manifest_request_obj)
    if obj.fea_blob_request is not None:
        Server.AddFeaBlobRequest(builder, fea_blob_request_obj)
    if obj.kernel_call is not None:
        Server.AddKernelCall(builder, kernel_call_obj)
    return Server.End(builder)
