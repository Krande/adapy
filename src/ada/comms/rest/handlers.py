"""RPC handlers for the REST viewer transport.

Each handler takes a deserialized Message + the storage backend and
returns a serialized response Message (bytes), shaped exactly like the
WS path so the frontend's existing dispatch (handleFlatbufferMessage)
works unchanged.

v1 implements the viewer subset:
- LIST_FILE_OBJECTS  — list keys in the configured bucket/prefix
- VIEW_FILE_OBJECT   — fetch a key (assumed already GLB) and return it
- GET_SERVER_INFO    — static process info

Other command types (procedures, multi-client, server lifecycle) only
make sense in desktop mode and return an ERROR Message.
"""

from __future__ import annotations

import os
import threading
from pathlib import PurePosixPath

from ada.comms.fb.fb_server_gen import ServerProcessInfoDC
from ada.comms.fb_wrap_deserializer import deserialize_root_message
from ada.comms.fb_wrap_model_gen import (
    CommandTypeDC,
    ErrorDC,
    FileObjectDC,
    FilePurposeDC,
    FileTypeDC,
    MessageDC,
    SceneDC,
    SceneOperationsDC,
    ServerDC,
    ServerReplyDC,
)
from ada.comms.fb_wrap_serializer import serialize_root_message
from ada.config import logger

from .storage import Storage

# The REST server has no persistent instance id; this is the value that
# appears in outgoing Messages so frontend logs are coherent.
SERVER_INSTANCE_ID = 0

_EXT_TO_TYPE: dict[str, FileTypeDC] = {
    ".ifc": FileTypeDC.IFC,
    ".glb": FileTypeDC.GLB,
    ".gltf": FileTypeDC.GLB,
    ".sqlite": FileTypeDC.SQLITE,
    ".db": FileTypeDC.SQLITE,
    ".xlsx": FileTypeDC.XLSX,
    ".csv": FileTypeDC.CSV,
}


def _infer_file_type(key: str) -> FileTypeDC | None:
    ext = PurePosixPath(key).suffix.lower()
    return _EXT_TO_TYPE.get(ext)


def _error_reply(message: MessageDC, msg: str) -> bytes:
    reply = MessageDC(
        instance_id=SERVER_INSTANCE_ID,
        command_type=CommandTypeDC.ERROR,
        target_id=message.instance_id,
        server_reply=ServerReplyDC(
            reply_to=message.command_type,
            error=ErrorDC(code=1, message=msg),
        ),
    )
    return serialize_root_message(reply)


async def _handle_list_file_objects(message: MessageDC, storage: Storage) -> bytes:
    files = await storage.list()
    file_objects: list[FileObjectDC] = []
    for entry in files:
        ftype = _infer_file_type(entry.key)
        if ftype is None:
            continue
        file_objects.append(
            FileObjectDC(
                name=entry.key,
                file_type=ftype,
                purpose=FilePurposeDC.DESIGN,
                filepath=entry.key,
            )
        )

    reply = MessageDC(
        instance_id=SERVER_INSTANCE_ID,
        command_type=CommandTypeDC.SERVER_REPLY,
        target_id=message.instance_id,
        server=ServerDC(all_file_objects=file_objects),
        server_reply=ServerReplyDC(reply_to=CommandTypeDC.LIST_FILE_OBJECTS),
    )
    return serialize_root_message(reply)


async def _handle_view_file_object(message: MessageDC, storage: Storage) -> bytes:
    key = ""
    if message.server is not None:
        key = (message.server.get_file_object_by_name or "").strip()
    if not key:
        return _error_reply(message, "view_file_object: missing file name")

    ftype = _infer_file_type(key)
    # v1: only GLB pass-through is supported. Conversion for IFC/STEP belongs
    # to a future upload pipeline that pre-converts before storage.
    if ftype != FileTypeDC.GLB:
        return _error_reply(
            message, f"view_file_object: only GLB files are supported in v1 (got {ftype})"
        )

    try:
        glb_bytes = await storage.get_bytes(key)
    except Exception as exc:  # obstore raises a generic Exception subclass on miss
        logger.warning("view_file_object: storage error for %s: %s", key, exc)
        return _error_reply(message, f"view_file_object: storage error: {exc}")

    glb_file = FileObjectDC(
        name=key,
        file_type=FileTypeDC.GLB,
        purpose=FilePurposeDC.DESIGN,
        filepath=key,
        filedata=glb_bytes,
    )
    reply = MessageDC(
        instance_id=SERVER_INSTANCE_ID,
        command_type=CommandTypeDC.SERVER_REPLY,
        target_id=message.instance_id,
        server_reply=ServerReplyDC(
            reply_to=CommandTypeDC.VIEW_FILE_OBJECT,
            file_objects=[glb_file],
        ),
        scene=SceneDC(operation=SceneOperationsDC.REPLACE, current_file=glb_file),
    )
    return serialize_root_message(reply)


async def _handle_get_server_info(message: MessageDC, _storage: Storage) -> bytes:
    log_path = ""
    for handler in logger.handlers:
        if hasattr(handler, "baseFilename"):
            log_path = handler.baseFilename  # type: ignore[attr-defined]
            break

    # threading.get_ident() returns a 64-bit value on most platforms, but
    # the wire schema declares thread_id as int32. Mask to fit.
    thread_id = threading.get_ident() & 0x7FFFFFFF

    reply = MessageDC(
        instance_id=SERVER_INSTANCE_ID,
        command_type=CommandTypeDC.SERVER_REPLY,
        target_id=message.instance_id,
        server_reply=ServerReplyDC(
            reply_to=CommandTypeDC.GET_SERVER_INFO,
            message="REST viewer API",
            process_info=ServerProcessInfoDC(
                pid=os.getpid(),
                thread_id=thread_id,
                log_file_path=log_path,
            ),
        ),
    )
    return serialize_root_message(reply)


_HANDLERS = {
    CommandTypeDC.LIST_FILE_OBJECTS: _handle_list_file_objects,
    CommandTypeDC.VIEW_FILE_OBJECT: _handle_view_file_object,
    CommandTypeDC.GET_SERVER_INFO: _handle_get_server_info,
}


async def dispatch(payload: bytes, storage: Storage) -> bytes | None:
    """Deserialize, dispatch, return serialized response (or None for no-reply)."""
    message = deserialize_root_message(payload)
    handler = _HANDLERS.get(message.command_type)
    if handler is None:
        logger.info("REST: unsupported command_type %s", message.command_type)
        return _error_reply(message, f"command_type {message.command_type} not supported in REST mode")
    return await handler(message, storage)
