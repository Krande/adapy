from __future__ import annotations

from typing import TYPE_CHECKING

from ada.comms.fb_wrap_model_gen import (
    CommandTypeDC,
    MessageDC,
    ProceduralModelLoadReplyDC,
    ServerReplyDC,
)
from ada.comms.fb_wrap_serializer import serialize_root_message
from ada.comms.msg_handling.on_error_reply import on_error_reply
from ada.comms.msg_handling.reply_to import reply_to
from ada.comms.msg_handling.save_procedural_model import (
    ProceduralModelIdError,
    _content_hash,
    _model_path,
)
from ada.config import logger

if TYPE_CHECKING:
    import pathlib

    from ada.comms.wsock.server import ConnectedClient, WebSocketAsyncServer


def load_procedural_model(
    server: WebSocketAsyncServer,
    client: ConnectedClient,
    message: MessageDC,
    base_dir: pathlib.Path | None = None,
) -> None:
    """Read back a procedural document ``SAVE_PROCEDURAL_MODEL`` previously wrote.

    Same directory rules, ``model_id`` validation and path containment as
    ``save_procedural_model`` (this is the read half of the same on-disk layout -- see that
    module's docstring). Replies with the document JSON plus its current sha256 ``content_hash``,
    so a client that loads a model can thread that hash back as ``expected_content_hash`` on its
    next save (the same contract the save reply already gives a saver, see
    ``docs/documents/ws_rest_parity.rst``).

    Rejects with a generic ERROR reply for a missing/invalid ``model_id`` or for a ``model_id``
    nothing has been saved under yet -- there is no revision-conflict distinction to make on a
    read, unlike ``save_procedural_model``'s ``CONFLICT_ERROR_CODE``.

    ``base_dir`` is a test seam (defaults to ``procedural_model_dir()``); production callers never
    pass it.
    """
    payload = message.server.load_procedural_model if message.server is not None else None
    if payload is None or not payload.model_id:
        on_error_reply(
            server,
            client,
            error_message="LOAD_PROCEDURAL_MODEL requires server.load_procedural_model.model_id",
            request_message=message,
        )
        return

    try:
        target = _model_path(payload.model_id, base_dir=base_dir)
    except ProceduralModelIdError as e:
        on_error_reply(server, client, error_message=str(e), request_message=message)
        return

    if not target.exists():
        on_error_reply(
            server,
            client,
            error_message=f"no procedural model has been saved under model_id {payload.model_id!r}",
            request_message=message,
        )
        return

    data = target.read_bytes()
    logger.info(f"Loaded procedural model {payload.model_id!r} ({len(data)} bytes) from {target}")

    reply_message = reply_to(
        message,
        instance_id=server.instance_id,
        command_type=CommandTypeDC.SERVER_REPLY,
        target_id=client.instance_id,
        target_group=client.group_type,
        server_reply=ServerReplyDC(
            reply_to=CommandTypeDC.LOAD_PROCEDURAL_MODEL,
            load_procedural_model=ProceduralModelLoadReplyDC(
                model_id=payload.model_id,
                doc_json=data.decode("utf-8"),
                content_hash=_content_hash(data),
            ),
        ),
    )
    server.send_message_threadsafe(client, serialize_root_message(reply_message))
