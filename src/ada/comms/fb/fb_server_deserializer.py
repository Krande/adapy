from ada.comms.fb.fb_base_deserializer import deserialize_error, deserialize_fileobject
from ada.comms.fb.fb_commands_gen import CommandTypeDC
from ada.comms.fb.fb_server_gen import ServerDC, ServerReplyDC


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
    )
