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

from .converter import derived_key_for, is_derived_key, is_supported_source
from .scope import Scope
from .storage import Storage

# The REST server has no persistent instance id; this is the value that
# appears in outgoing Messages so frontend logs are coherent.
SERVER_INSTANCE_ID = 0

# Direct mappings to wire-level FileTypeDC values.
_EXT_TO_TYPE: dict[str, FileTypeDC] = {
    ".ifc": FileTypeDC.IFC,
    ".glb": FileTypeDC.GLB,
    ".gltf": FileTypeDC.GLB,
    ".sqlite": FileTypeDC.SQLITE,
    ".db": FileTypeDC.SQLITE,
    ".xlsx": FileTypeDC.XLSX,
    ".csv": FileTypeDC.CSV,
}

# Convertable CAD/FEM source formats with no dedicated FileTypeDC entry.
# We flag them as IFC on the wire so the frontend's "non-GLB → needs
# conversion" branch fires; the actual format is recoverable from the
# filename extension if needed.
_CONVERTABLE_AS_IFC: frozenset[str] = frozenset(
    {".step", ".stp", ".xml", ".inp", ".fem", ".sat", ".acis", ".obj", ".stl", ".ply", ".dae", ".off"}
)


def _infer_file_type(key: str) -> FileTypeDC | None:
    ext = PurePosixPath(key).suffix.lower()
    if ext in _EXT_TO_TYPE:
        return _EXT_TO_TYPE[ext]
    if ext in _CONVERTABLE_AS_IFC:
        return FileTypeDC.IFC
    return None


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
    # Phase 2B: legacy RPC handlers operate on the shared scope only.
    # Phase 2C surfaces a scope arg via a separate, scope-aware RPC if
    # the WS-style envelope ever needs multi-tenant access.
    files = await storage.list(Scope.shared())
    file_objects: list[FileObjectDC] = []
    for entry in files:
        # Hide internal derived blobs from the user-facing file list.
        if is_derived_key(entry.key):
            continue
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

    # Resolve the GLB to actually serve. A direct GLB key is served as-is;
    # for any other supported source we serve its sibling derived GLB if
    # it has been converted already. Frontend kicks off /api/convert when
    # the derived blob is missing.
    glb_key: str | None = None
    if PurePosixPath(key).suffix.lower() == ".glb":
        glb_key = key
    elif is_supported_source(key):
        candidate = derived_key_for(key)
        if await storage.exists(Scope.shared(), candidate):
            glb_key = candidate

    if glb_key is None:
        return _error_reply(
            message,
            f"view_file_object: no GLB available for {key!r}; convert it first",
        )

    try:
        glb_bytes = await storage.get_bytes(Scope.shared(), glb_key)
    except Exception as exc:  # obstore raises a generic Exception subclass on miss
        logger.warning("view_file_object: storage error for %s: %s", glb_key, exc)
        return _error_reply(message, f"view_file_object: storage error: {exc}")

    glb_file = FileObjectDC(
        name=key,
        file_type=FileTypeDC.GLB,
        purpose=FilePurposeDC.DESIGN,
        filepath=glb_key,
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
