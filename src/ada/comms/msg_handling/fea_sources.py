"""FEA results over the websocket: the server side of ``FEAResult.show()``.

The REST viewer never receives an FEA result as one GLB. A bake
(:func:`ada.fem.results.artefacts.bake_artefacts`) writes a manifest, the mesh GLB
and one blob per field, and the browser reads the manifest, then the mesh, then
one step of one field at a time. These handlers serve the same bake over the
websocket, so the viewer's streaming FEA loader runs unchanged on either
transport -- only where the bytes come from differs.

A bake lives in a directory on this machine. The local process that wrote it
registers it under a source key (REGISTER_FEA_SOURCE); viewers then read it by
that key (GET_FEA_MANIFEST, GET_FEA_BLOB). Nothing outside a registered bake
directory is ever read: a filename that resolves elsewhere is refused.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ada.comms.fb_wrap_model_gen import (
    CommandTypeDC,
    FeaBlobReplyDC,
    FeaManifestReplyDC,
    FeaSourceInfoDC,
    MessageDC,
    ServerDC,
    ServerReplyDC,
    TargetTypeDC,
)
from ada.comms.fb_wrap_serializer import serialize_root_message
from ada.comms.msg_handling.on_error_reply import on_error_reply
from ada.comms.msg_handling.reply_to import reply_to
from ada.config import logger

if TYPE_CHECKING:
    from ada.comms.wsock.server import ConnectedClient, WebSocketAsyncServer

MANIFEST_NAME = "fea.manifest.json"


@dataclass(frozen=True)
class FeaSource:
    bake_dir: pathlib.Path
    bake_id: str


class FeaSourceError(ValueError):
    """A request named a source or file this server cannot serve."""


def _sources(server: WebSocketAsyncServer) -> dict[str, FeaSource]:
    # Kept on the server object, created on first use, so a server built without
    # this module (or a test double) needs no extra setup.
    sources = getattr(server, "fea_sources", None)
    if sources is None:
        sources = {}
        server.fea_sources = sources
    return sources


def _source(server: WebSocketAsyncServer, source_key: str) -> FeaSource:
    source = _sources(server).get(source_key or "")
    if source is None:
        raise FeaSourceError(f"no FEA source is registered under {source_key!r}")
    return source


def _bake_file(source: FeaSource, filename: str) -> pathlib.Path:
    """``filename`` inside ``source``'s bake directory, or FeaSourceError."""
    root = source.bake_dir.resolve()
    target = (root / (filename or "").lstrip("/\\")).resolve()
    if target == root or root not in target.parents:
        raise FeaSourceError(f"{filename!r} is not a file of this FEA bake")
    if not target.is_file():
        raise FeaSourceError(f"{filename!r} is not in the FEA bake")
    return target


def open_source_message(server: WebSocketAsyncServer, info: FeaSourceInfoDC) -> bytes:
    """The OPEN_FEA_SOURCE push telling a viewer to load ``info``'s source."""
    return serialize_root_message(
        MessageDC(
            instance_id=server.instance_id,
            command_type=CommandTypeDC.OPEN_FEA_SOURCE,
            target_group=TargetTypeDC.WEB,
            server=ServerDC(open_fea_source=info),
        )
    )


def register_fea_source(server: WebSocketAsyncServer, client: ConnectedClient, message: MessageDC) -> None:
    payload = message.server.register_fea_source if message.server is not None else None
    if payload is None or not payload.source_key or not payload.bake_dir:
        on_error_reply(
            server,
            client,
            error_message="REGISTER_FEA_SOURCE requires server.register_fea_source.source_key and bake_dir",
            request_message=message,
        )
        return

    bake_dir = pathlib.Path(payload.bake_dir)
    if not (bake_dir / MANIFEST_NAME).is_file():
        on_error_reply(server, client, error_message=f"{bake_dir} holds no {MANIFEST_NAME}", request_message=message)
        return

    info = FeaSourceInfoDC(source_key=payload.source_key, bake_id=payload.bake_id or "")
    _sources(server)[payload.source_key] = FeaSource(bake_dir=bake_dir, bake_id=info.bake_id)
    logger.info(f"Registered FEA source {payload.source_key!r} -> {bake_dir}")

    reply = reply_to(
        message,
        instance_id=server.instance_id,
        command_type=CommandTypeDC.SERVER_REPLY,
        target_id=client.instance_id,
        target_group=client.group_type,
        server_reply=ServerReplyDC(reply_to=CommandTypeDC.REGISTER_FEA_SOURCE, fea_source=info),
    )
    server.send_message_threadsafe(client, serialize_root_message(reply))

    if payload.open_in_viewer:
        # Remembered, so a viewer that connects (or reloads) after this still opens it.
        server.fea_open_source = info
        push = open_source_message(server, info)
        for web_client in list(server.connected_web_clients):
            server.send_message_threadsafe(web_client, push)


def get_fea_manifest(server: WebSocketAsyncServer, client: ConnectedClient, message: MessageDC) -> None:
    payload = message.server.fea_manifest_request if message.server is not None else None
    try:
        source_key = payload.source_key if payload is not None else ""
        source = _source(server, source_key)
        manifest_json = _bake_file(source, MANIFEST_NAME).read_text(encoding="utf-8")
    except (FeaSourceError, OSError) as e:
        on_error_reply(server, client, error_message=str(e), request_message=message)
        return

    reply = reply_to(
        message,
        instance_id=server.instance_id,
        command_type=CommandTypeDC.SERVER_REPLY,
        target_id=client.instance_id,
        target_group=client.group_type,
        server_reply=ServerReplyDC(
            reply_to=CommandTypeDC.GET_FEA_MANIFEST,
            fea_manifest=FeaManifestReplyDC(source_key=source_key, bake_id=source.bake_id, manifest_json=manifest_json),
        ),
    )
    server.send_message_threadsafe(client, serialize_root_message(reply))


def read_blob(source: FeaSource, filename: str, ranged: bool, start: int, end: int) -> tuple[bytes, bool, int]:
    """``(bytes, ranged, file size)`` of ``filename``: the inclusive window ``start``..``end``
    when ``ranged`` (clamped to the file), else the whole file."""
    path = _bake_file(source, filename)
    size = path.stat().st_size
    if not ranged:
        return path.read_bytes(), False, size
    start = max(0, int(start or 0))
    end = min(size - 1, int(end if end is not None else size - 1))
    if end < start:
        return b"", True, size
    with open(path, "rb") as f:
        f.seek(start)
        return f.read(end - start + 1), True, size


def get_fea_blob(server: WebSocketAsyncServer, client: ConnectedClient, message: MessageDC) -> None:
    payload = message.server.fea_blob_request if message.server is not None else None
    if payload is None:
        on_error_reply(
            server, client, error_message="GET_FEA_BLOB requires server.fea_blob_request", request_message=message
        )
        return
    try:
        source = _source(server, payload.source_key)
        data, ranged, size = read_blob(source, payload.filename, bool(payload.ranged), payload.start, payload.end)
    except (FeaSourceError, OSError) as e:
        on_error_reply(server, client, error_message=str(e), request_message=message)
        return

    reply = reply_to(
        message,
        instance_id=server.instance_id,
        command_type=CommandTypeDC.SERVER_REPLY,
        target_id=client.instance_id,
        target_group=client.group_type,
        server_reply=ServerReplyDC(
            reply_to=CommandTypeDC.GET_FEA_BLOB,
            fea_blob=FeaBlobReplyDC(
                source_key=payload.source_key,
                filename=payload.filename,
                data=data,
                ranged=ranged,
                total_size=size,
            ),
        ),
    )
    server.send_message_threadsafe(client, serialize_root_message(reply))
