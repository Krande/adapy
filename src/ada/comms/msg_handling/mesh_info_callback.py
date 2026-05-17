from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ada.comms.fb_wrap_model_gen import (
    CommandTypeDC,
    MeshInfoDC,
    MessageDC,
    TargetTypeDC,
)
from ada.comms.fb_wrap_serializer import serialize_root_message
from ada.comms.msg_handling.object_metadata import populate_for_file_object
from ada.config import logger

if TYPE_CHECKING:
    from ada.comms.wsock.server import ConnectedClient, WebSocketAsyncServer


def mesh_info_callback(server: WebSocketAsyncServer, client: ConnectedClient, message: MessageDC) -> None:
    """Resolve metadata for a clicked viewer element and reply with JSON.

    The frontend sends the logical object name (same string shown in the
    info box) plus the source file name. We look up the cached structured
    dict from :func:`build_object_meta_from_assembly` and ship just that
    — the cross-model link is resolved on the frontend via the
    ``ADA_EXT_data`` extension's ``assembly_guid`` / ``parent_object_guid``
    fields, so the server doesn't need to touch lineage here."""
    info = message.mesh_info
    if info is None:
        return
    object_name = info.object_name
    file_name = info.file_name or _infer_file_name(server)
    if not object_name:
        logger.debug("mesh_info_callback: empty object_name in request; nothing to look up")
        _reply(server, client, info, None)
        return

    meta = _lookup_meta(server, file_name, object_name)
    _reply(server, client, info, meta)


def _lookup_meta(server: WebSocketAsyncServer, file_name: str | None, object_name: str) -> dict | None:
    if file_name:
        # Build the index on demand the first time anyone clicks in this
        # file. Spreading the cost across the first click (rather than the
        # initial file ingest) keeps the load path fast for users who only
        # want to look at the geometry.
        if file_name not in server.scene.object_meta or not server.scene.object_meta[file_name]:
            fo = server.scene.get_file_object(file_name)
            if fo is not None:
                populate_for_file_object(server.scene, fo)
        per_file = server.scene.object_meta.get(file_name, {})
        if object_name in per_file:
            return per_file[object_name]
    # Fallback: name collisions across files are uncommon, so a sweep
    # over all loaded files finds the right entry when the request
    # arrives without a file_name (e.g. from an older client).
    for fname, per_file in server.scene.object_meta.items():
        if object_name in per_file:
            return per_file[object_name]
    return None


def _infer_file_name(server: WebSocketAsyncServer) -> str | None:
    """Older clients don't send ``file_name``. Fall back to the only loaded
    file when unambiguous; otherwise return None and let ``_lookup_meta``
    do a cross-file sweep."""
    if len(server.scene.file_objects) == 1:
        return server.scene.file_objects[0].name
    return None


def _reply(
    server: WebSocketAsyncServer,
    client: ConnectedClient,
    request_info,
    payload: dict | None,
) -> None:
    json_data = json.dumps(payload) if payload is not None else ""
    mesh_info = MeshInfoDC(
        object_name=request_info.object_name,
        face_index=request_info.face_index,
        json_data=json_data,
        file_name=request_info.file_name,
    )
    reply_message = MessageDC(
        instance_id=server.instance_id,
        command_type=CommandTypeDC.MESH_INFO_REPLY,
        mesh_info=mesh_info,
        target_id=client.instance_id,
        target_group=TargetTypeDC.WEB,
    )
    fb_message = serialize_root_message(reply_message)
    server.send_message_threadsafe(client, fb_message)
