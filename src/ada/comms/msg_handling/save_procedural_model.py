from __future__ import annotations

import hashlib
import os
import pathlib
import re
import sys
from typing import TYPE_CHECKING

from ada.comms.fb_wrap_model_gen import (
    CommandTypeDC,
    MessageDC,
    ProceduralModelSaveReplyDC,
    ServerReplyDC,
)
from ada.comms.fb_wrap_serializer import serialize_root_message
from ada.comms.msg_handling.on_error_reply import on_error_reply
from ada.comms.msg_handling.reply_to import reply_to
from ada.config import logger

if TYPE_CHECKING:
    from ada.comms.wsock.server import ConnectedClient, WebSocketAsyncServer

# Overrides where SAVE_PROCEDURAL_MODEL writes its documents. Named in the
# same style as the other direct-env-read ADA_* switches (e.g.
# ADA_STREAM_TESS_PIPELINE) rather than a Config()/ada_config.toml entry: this
# is a single-process, single-user setting -- there is no deployment-config
# story for "where does THIS laptop's viewer keep its models".
ENV_PROCEDURAL_MODEL_DIR = "ADA_PROCEDURAL_MODEL_DIR"

# A dedicated subdirectory rather than writing straight next to the source
# file: keeps the source tree free of model_id.json litter, and gives
# `_model_path` one fixed root to check path-containment against regardless
# of where that root itself came from (default vs override).
_MODELS_SUBDIR = ".ada_procedural_models"

# Mirrors the REST commit endpoint's HTTP 409 for the same situation -- a
# stale base revision -- so a client that already knows to special-case 409
# recognises this one too. See docs/documents/ws_rest_parity.rst, "revisions
# and optimistic concurrency".
CONFLICT_ERROR_CODE = 409

# An opaque token: letters/digits/'.'/'_'/'-' only, starting with a letter or
# digit, 1-128 characters. No '/' or '\\' means no separators to traverse
# with, so this alone is what stops a client steering `model_id` into a path
# (see the ws/REST parity plan's "model id: opaque token vs file path" trap);
# `_model_path` re-checks containment on the resolved path anyway.
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ProceduralModelIdError(ValueError):
    """``model_id`` failed validation. Raised before anything touches disk."""


def default_procedural_model_dir() -> pathlib.Path:
    """Next to the source file the model was shown from.

    This websocket server process *is* the process that called
    ``assembly.show()`` (see docs/documents/ws_rest_parity.rst) -- there is no
    separate "which file opened this" to track down; it is simply whatever
    ``__main__`` is. Falls back to the current working directory for a
    REPL/notebook session, which has no ``__main__`` file.
    """
    main = sys.modules.get("__main__")
    main_file = getattr(main, "__file__", None)
    if main_file:
        try:
            return pathlib.Path(main_file).resolve().parent
        except OSError:
            pass
    return pathlib.Path.cwd()


def procedural_model_dir() -> pathlib.Path:
    """Directory ``SAVE_PROCEDURAL_MODEL`` writes into.

    Honours ``ADA_PROCEDURAL_MODEL_DIR`` when set; otherwise
    ``default_procedural_model_dir()``, plus the fixed ``_MODELS_SUBDIR``.
    """
    override = os.environ.get(ENV_PROCEDURAL_MODEL_DIR)
    base = pathlib.Path(override).expanduser() if override else default_procedural_model_dir()
    return base / _MODELS_SUBDIR


def _validate_model_id(model_id: str | None) -> str:
    """An opaque token the client invented -- never a file path. Rejects
    anything that isn't a plain, boring identifier, which is stricter than it
    needs to be for traversal alone (no separators means no traversal
    regardless of dots), but a save verb writing to disk from a client-
    supplied string should not need to reason about which characters are
    "probably" safe."""
    if not model_id or not _MODEL_ID_RE.fullmatch(model_id):
        raise ProceduralModelIdError(
            f"invalid model_id {model_id!r}: must be 1-128 characters of letters, digits, '.', "
            f"'_' or '-', starting with a letter or digit -- it is an opaque id, never a file path"
        )
    return model_id


def _model_path(model_id: str, base_dir: pathlib.Path | None = None) -> pathlib.Path:
    validated = _validate_model_id(model_id)
    base_dir = base_dir if base_dir is not None else procedural_model_dir()
    base_dir.mkdir(parents=True, exist_ok=True)
    base_resolved = base_dir.resolve()
    target = (base_resolved / f"{validated}.json").resolve()
    if target.parent != base_resolved:
        # Defence in depth: the regex above already forbids separators (and
        # therefore ".." segments), so this should be unreachable -- but a
        # handler that writes client-addressed files to disk does not get to
        # rely on a regex alone.
        raise ProceduralModelIdError(f"model_id {model_id!r} resolves outside the model directory")
    return target


def _content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_procedural_model(
    server: WebSocketAsyncServer,
    client: ConnectedClient,
    message: MessageDC,
    base_dir: pathlib.Path | None = None,
) -> None:
    """Write a procedural document to local disk, keyed by an opaque
    ``model_id``, with a content-hash revision.

    See docs/documents/ws_rest_parity.rst, step 3 ("SAVE_PROCEDURAL_MODEL").
    Replies with the new content hash on success. Rejects with an ERROR reply:
    a generic one for a malformed request (missing/invalid ``model_id``), or
    one carrying ``code=CONFLICT_ERROR_CODE`` when ``expected_content_hash``
    was given and does not match what is currently on disk (including "there
    is nothing on disk yet" -- an expected hash implies the caller believes a
    prior save exists).

    ``base_dir`` is a test seam (defaults to ``procedural_model_dir()``);
    production callers never pass it.
    """
    payload = message.server.save_procedural_model if message.server is not None else None
    if payload is None or not payload.model_id:
        on_error_reply(
            server,
            client,
            error_message="SAVE_PROCEDURAL_MODEL requires server.save_procedural_model.model_id",
            request_message=message,
        )
        return

    try:
        target = _model_path(payload.model_id, base_dir=base_dir)
    except ProceduralModelIdError as e:
        on_error_reply(server, client, error_message=str(e), request_message=message)
        return

    expected_hash = payload.expected_content_hash or None

    existing_bytes = target.read_bytes() if target.exists() else None
    current_hash = _content_hash(existing_bytes) if existing_bytes is not None else None

    if expected_hash:
        if current_hash is None:
            on_error_reply(
                server,
                client,
                error_message=(
                    f"expected revision {expected_hash!r} for model_id {payload.model_id!r}, "
                    f"but no model has been saved under that id yet"
                ),
                request_message=message,
                code=CONFLICT_ERROR_CODE,
            )
            return
        if current_hash != expected_hash:
            on_error_reply(
                server,
                client,
                error_message=(
                    f"revision conflict saving {payload.model_id!r}: expected {expected_hash!r}, "
                    f"found {current_hash!r} on disk"
                ),
                request_message=message,
                code=CONFLICT_ERROR_CODE,
            )
            return

    new_bytes = (payload.doc_json or "").encode("utf-8")
    new_hash = _content_hash(new_bytes)

    # Write-then-rename so a concurrent reader (or a future LOAD handler)
    # never observes a half-written file. Same-directory temp file so the
    # rename is atomic (no cross-filesystem fallback needed).
    tmp_path = target.with_name(f"{target.name}.tmp-{os.getpid()}")
    tmp_path.write_bytes(new_bytes)
    os.replace(tmp_path, target)

    logger.info(f"Saved procedural model {payload.model_id!r} ({len(new_bytes)} bytes) to {target}")

    reply_message = reply_to(
        message,
        instance_id=server.instance_id,
        command_type=CommandTypeDC.SERVER_REPLY,
        target_id=client.instance_id,
        target_group=client.group_type,
        server_reply=ServerReplyDC(
            reply_to=CommandTypeDC.SAVE_PROCEDURAL_MODEL,
            save_procedural_model=ProceduralModelSaveReplyDC(model_id=payload.model_id, content_hash=new_hash),
        ),
    )
    server.send_message_threadsafe(client, serialize_root_message(reply_message))
