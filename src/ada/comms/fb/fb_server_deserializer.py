from ada.comms.fb.fb_base_deserializer import deserialize_error, deserialize_fileobject
from ada.comms.fb.fb_commands_gen import CommandTypeDC
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


def deserialize_serverprocessinfo(fb_obj) -> ServerProcessInfoDC | None:
    if fb_obj is None:
        return None

    return ServerProcessInfoDC(
        pid=fb_obj.Pid(),
        thread_id=fb_obj.ThreadId(),
        log_file_path=fb_obj.LogFilePath().decode("utf-8") if fb_obj.LogFilePath() is not None else None,
    )


def deserialize_proceduralmodelsave(fb_obj) -> ProceduralModelSaveDC | None:
    if fb_obj is None:
        return None

    return ProceduralModelSaveDC(
        model_id=fb_obj.ModelId().decode("utf-8") if fb_obj.ModelId() is not None else None,
        doc_json=fb_obj.DocJson().decode("utf-8") if fb_obj.DocJson() is not None else None,
        expected_content_hash=(
            fb_obj.ExpectedContentHash().decode("utf-8") if fb_obj.ExpectedContentHash() is not None else None
        ),
    )


def deserialize_proceduralmodelsavereply(fb_obj) -> ProceduralModelSaveReplyDC | None:
    if fb_obj is None:
        return None

    return ProceduralModelSaveReplyDC(
        model_id=fb_obj.ModelId().decode("utf-8") if fb_obj.ModelId() is not None else None,
        content_hash=fb_obj.ContentHash().decode("utf-8") if fb_obj.ContentHash() is not None else None,
    )


def deserialize_proceduralmodellistentry(fb_obj) -> ProceduralModelListEntryDC | None:
    if fb_obj is None:
        return None

    return ProceduralModelListEntryDC(
        model_id=fb_obj.ModelId().decode("utf-8") if fb_obj.ModelId() is not None else None,
        content_hash=fb_obj.ContentHash().decode("utf-8") if fb_obj.ContentHash() is not None else None,
        modified_at=fb_obj.ModifiedAt(),
        size_bytes=fb_obj.SizeBytes(),
    )


def deserialize_proceduralmodellistreply(fb_obj) -> ProceduralModelListReplyDC | None:
    if fb_obj is None:
        return None

    return ProceduralModelListReplyDC(
        entries=(
            [deserialize_proceduralmodellistentry(fb_obj.Entries(i)) for i in range(fb_obj.EntriesLength())]
            if fb_obj.EntriesLength() > 0
            else None
        )
    )


def deserialize_proceduralmodelload(fb_obj) -> ProceduralModelLoadDC | None:
    if fb_obj is None:
        return None

    return ProceduralModelLoadDC(model_id=fb_obj.ModelId().decode("utf-8") if fb_obj.ModelId() is not None else None)


def deserialize_proceduralmodelloadreply(fb_obj) -> ProceduralModelLoadReplyDC | None:
    if fb_obj is None:
        return None

    return ProceduralModelLoadReplyDC(
        model_id=fb_obj.ModelId().decode("utf-8") if fb_obj.ModelId() is not None else None,
        doc_json=fb_obj.DocJson().decode("utf-8") if fb_obj.DocJson() is not None else None,
        content_hash=fb_obj.ContentHash().decode("utf-8") if fb_obj.ContentHash() is not None else None,
    )


def deserialize_feasourceregister(fb_obj) -> FeaSourceRegisterDC | None:
    if fb_obj is None:
        return None

    return FeaSourceRegisterDC(
        source_key=fb_obj.SourceKey().decode("utf-8") if fb_obj.SourceKey() is not None else None,
        bake_dir=fb_obj.BakeDir().decode("utf-8") if fb_obj.BakeDir() is not None else None,
        bake_id=fb_obj.BakeId().decode("utf-8") if fb_obj.BakeId() is not None else None,
        open_in_viewer=fb_obj.OpenInViewer(),
    )


def deserialize_feasourceinfo(fb_obj) -> FeaSourceInfoDC | None:
    if fb_obj is None:
        return None

    return FeaSourceInfoDC(
        source_key=fb_obj.SourceKey().decode("utf-8") if fb_obj.SourceKey() is not None else None,
        bake_id=fb_obj.BakeId().decode("utf-8") if fb_obj.BakeId() is not None else None,
    )


def deserialize_feamanifestrequest(fb_obj) -> FeaManifestRequestDC | None:
    if fb_obj is None:
        return None

    return FeaManifestRequestDC(
        source_key=fb_obj.SourceKey().decode("utf-8") if fb_obj.SourceKey() is not None else None
    )


def deserialize_feamanifestreply(fb_obj) -> FeaManifestReplyDC | None:
    if fb_obj is None:
        return None

    return FeaManifestReplyDC(
        source_key=fb_obj.SourceKey().decode("utf-8") if fb_obj.SourceKey() is not None else None,
        bake_id=fb_obj.BakeId().decode("utf-8") if fb_obj.BakeId() is not None else None,
        manifest_json=fb_obj.ManifestJson().decode("utf-8") if fb_obj.ManifestJson() is not None else None,
    )


def deserialize_feablobrequest(fb_obj) -> FeaBlobRequestDC | None:
    if fb_obj is None:
        return None

    return FeaBlobRequestDC(
        source_key=fb_obj.SourceKey().decode("utf-8") if fb_obj.SourceKey() is not None else None,
        filename=fb_obj.Filename().decode("utf-8") if fb_obj.Filename() is not None else None,
        ranged=fb_obj.Ranged(),
        start=fb_obj.Start(),
        end=fb_obj.End(),
    )


def deserialize_feablobreply(fb_obj) -> FeaBlobReplyDC | None:
    if fb_obj is None:
        return None

    return FeaBlobReplyDC(
        source_key=fb_obj.SourceKey().decode("utf-8") if fb_obj.SourceKey() is not None else None,
        filename=fb_obj.Filename().decode("utf-8") if fb_obj.Filename() is not None else None,
        data=bytes(fb_obj.DataAsNumpy()) if fb_obj.DataLength() > 0 else None,
        ranged=fb_obj.Ranged(),
        total_size=fb_obj.TotalSize(),
    )


def deserialize_kernelcall(fb_obj) -> KernelCallDC | None:
    if fb_obj is None:
        return None

    return KernelCallDC(
        function=fb_obj.Function().decode("utf-8") if fb_obj.Function() is not None else None,
        args_json=fb_obj.ArgsJson().decode("utf-8") if fb_obj.ArgsJson() is not None else None,
        data=bytes(fb_obj.DataAsNumpy()) if fb_obj.DataLength() > 0 else None,
    )


def deserialize_kernelreply(fb_obj) -> KernelReplyDC | None:
    if fb_obj is None:
        return None

    return KernelReplyDC(
        function=fb_obj.Function().decode("utf-8") if fb_obj.Function() is not None else None,
        result_json=fb_obj.ResultJson().decode("utf-8") if fb_obj.ResultJson() is not None else None,
        data=bytes(fb_obj.DataAsNumpy()) if fb_obj.DataLength() > 0 else None,
    )


def deserialize_serverreply(fb_obj) -> ServerReplyDC | None:
    if fb_obj is None:
        return None

    return ServerReplyDC(
        message=fb_obj.Message().decode("utf-8") if fb_obj.Message() is not None else None,
        file_objects=(
            [deserialize_fileobject(fb_obj.FileObjects(i)) for i in range(fb_obj.FileObjectsLength())]
            if fb_obj.FileObjectsLength() > 0
            else None
        ),
        reply_to=CommandTypeDC(fb_obj.ReplyTo()),
        error=deserialize_error(fb_obj.Error()),
        process_info=deserialize_serverprocessinfo(fb_obj.ProcessInfo()),
        save_procedural_model=deserialize_proceduralmodelsavereply(fb_obj.SaveProceduralModel()),
        list_procedural_models=deserialize_proceduralmodellistreply(fb_obj.ListProceduralModels()),
        load_procedural_model=deserialize_proceduralmodelloadreply(fb_obj.LoadProceduralModel()),
        fea_source=deserialize_feasourceinfo(fb_obj.FeaSource()),
        fea_manifest=deserialize_feamanifestreply(fb_obj.FeaManifest()),
        fea_blob=deserialize_feablobreply(fb_obj.FeaBlob()),
        kernel=deserialize_kernelreply(fb_obj.Kernel()),
    )


def deserialize_server(fb_obj) -> ServerDC | None:
    if fb_obj is None:
        return None

    return ServerDC(
        new_file_object=deserialize_fileobject(fb_obj.NewFileObject()),
        all_file_objects=(
            [deserialize_fileobject(fb_obj.AllFileObjects(i)) for i in range(fb_obj.AllFileObjectsLength())]
            if fb_obj.AllFileObjectsLength() > 0
            else None
        ),
        get_file_object_by_name=(
            fb_obj.GetFileObjectByName().decode("utf-8") if fb_obj.GetFileObjectByName() is not None else None
        ),
        get_file_object_by_path=(
            fb_obj.GetFileObjectByPath().decode("utf-8") if fb_obj.GetFileObjectByPath() is not None else None
        ),
        delete_file_object=deserialize_fileobject(fb_obj.DeleteFileObject()),
        start_file_in_local_app=deserialize_fileobject(fb_obj.StartFileInLocalApp()),
        save_procedural_model=deserialize_proceduralmodelsave(fb_obj.SaveProceduralModel()),
        load_procedural_model=deserialize_proceduralmodelload(fb_obj.LoadProceduralModel()),
        register_fea_source=deserialize_feasourceregister(fb_obj.RegisterFeaSource()),
        open_fea_source=deserialize_feasourceinfo(fb_obj.OpenFeaSource()),
        fea_manifest_request=deserialize_feamanifestrequest(fb_obj.FeaManifestRequest()),
        fea_blob_request=deserialize_feablobrequest(fb_obj.FeaBlobRequest()),
        kernel_call=deserialize_kernelcall(fb_obj.KernelCall()),
    )
