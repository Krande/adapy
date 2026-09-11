from __future__ import annotations

from typing import TYPE_CHECKING

from ada.comms.fb_wrap_model_gen import (
    CommandTypeDC,
    MessageDC,
    ProceduralModelListEntryDC,
    ProceduralModelListReplyDC,
    ServerReplyDC,
)
from ada.comms.fb_wrap_serializer import serialize_root_message
from ada.comms.msg_handling.reply_to import reply_to
from ada.comms.msg_handling.save_procedural_model import (
    _MODEL_ID_RE,
    _content_hash,
    procedural_model_dir,
)
from ada.config import logger

if TYPE_CHECKING:
    import pathlib

    from ada.comms.wsock.server import ConnectedClient, WebSocketAsyncServer


def list_procedural_models(
    server: WebSocketAsyncServer,
    client: ConnectedClient,
    message: MessageDC,
    base_dir: pathlib.Path | None = None,
) -> None:
    """Enumerate the documents ``SAVE_PROCEDURAL_MODEL`` has written to local disk.

    Reads the same directory ``save_procedural_model`` writes into (``base_dir``, defaulting to
    ``procedural_model_dir()``) and reports one entry per model: ``model_id``, its current
    ``content_hash`` (the same sha256 hex digest a save/load reply carries, so a browser can spot
    a stale copy without a separate load), ``modified_at`` (the file's mtime, Unix
    milliseconds -- display/sort only, never a concurrency token) and its size in bytes.

    Never errors on an empty or missing directory -- an empty list is the correct answer for "no
    models saved yet", not a failure. A stray, non-model file dropped into the directory by hand
    (or a save's ``*.json.tmp-<pid>`` temp file, which the glob below does not even match) is
    skipped rather than surfaced as a bogus entry, using the same model_id allow-list
    ``save_procedural_model`` validates against.

    ``base_dir`` is a test seam (defaults to ``procedural_model_dir()``); production callers
    never pass it.
    """
    base_dir = base_dir if base_dir is not None else procedural_model_dir()
    base_dir.mkdir(parents=True, exist_ok=True)

    entries: list[ProceduralModelListEntryDC] = []
    for path in sorted(base_dir.glob("*.json")):
        model_id = path.stem
        if not _MODEL_ID_RE.fullmatch(model_id):
            continue
        try:
            data = path.read_bytes()
            modified_at = path.stat().st_mtime
        except OSError:
            # Removed between the glob and the read (another process/tab saving/deleting
            # concurrently) -- just leave it out of this listing rather than fail the whole request.
            continue
        entries.append(
            ProceduralModelListEntryDC(
                model_id=model_id,
                content_hash=_content_hash(data),
                modified_at=int(modified_at * 1000),
                size_bytes=len(data),
            )
        )

    logger.info(f"Listed {len(entries)} procedural model(s) in {base_dir} for {client.instance_id}")

    reply_message = reply_to(
        message,
        instance_id=server.instance_id,
        command_type=CommandTypeDC.SERVER_REPLY,
        target_id=client.instance_id,
        target_group=client.group_type,
        server_reply=ServerReplyDC(
            reply_to=CommandTypeDC.LIST_PROCEDURAL_MODELS,
            list_procedural_models=ProceduralModelListReplyDC(entries=entries),
        ),
    )
    server.send_message_threadsafe(client, serialize_root_message(reply_message))
