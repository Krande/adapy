"""Viewer control on the websocket envelope: the FlatBuffer ``Message`` that carries a command (or a
batch) to a viewer and its answer and state back.

``VIEWER_COMMAND`` (to a viewer): ``request_id`` is the command id, ``payload_json`` the command as
``{"command": {...}}`` or ``{"commands": [...]}`` (``ada.comms.rest.viewer_control.CommandRequest``).
``VIEWER_ANSWER`` (from the viewer): ``request_id`` echoed, ``payload_json`` the outcome
``{ok, error, result, steps?, state}``. ``VIEWER_STATE`` (from the viewer, unasked): ``payload_json``
what it shows. The same three run over the REST server's ``/api/viewer/ws`` and through the
desktop websocket server, which relays them between a local client and a viewer.

LIGHT: the generated FlatBuffer code and the stdlib.
"""

from __future__ import annotations

import json
from typing import Any

from ada.comms.fb.fb_wsock_deserializer import deserialize_root_message
from ada.comms.fb.fb_wsock_serializer import serialize_root_message
from ada.comms.fb_wrap_model_gen import CommandTypeDC, MessageDC, TargetTypeDC

__all__ = ["answer_message", "command_message", "payload_of", "read", "state_message"]


def _message(command_type: CommandTypeDC, payload: Any, **fields: Any) -> bytes:
    return serialize_root_message(
        MessageDC(command_type=command_type, payload_json=json.dumps(payload, default=str), **fields)
    )


def command_message(
    command_id: str,
    payload: dict,
    *,
    instance_id: int | None = None,
    target_id: int | None = None,
    client_type: TargetTypeDC = TargetTypeDC.SERVER,
) -> bytes:
    """A ``VIEWER_COMMAND`` for a viewer (``target_id``: which one, where a relay routes)."""
    return _message(
        CommandTypeDC.VIEWER_COMMAND,
        payload,
        request_id=command_id,
        instance_id=instance_id,
        target_group=TargetTypeDC.WEB,
        target_id=target_id,
        client_type=client_type,
    )


def answer_message(
    command_id: str, answer: dict, *, instance_id: int | None = None, target_id: int | None = None
) -> bytes:
    """A viewer's ``VIEWER_ANSWER`` (what the browser sends; here for tests and Python viewers)."""
    return _message(
        CommandTypeDC.VIEWER_ANSWER,
        answer,
        request_id=command_id,
        instance_id=instance_id,
        target_id=target_id,
        target_group=TargetTypeDC.LOCAL if target_id is not None else TargetTypeDC.SERVER,
        client_type=TargetTypeDC.WEB,
    )


def state_message(state: dict, *, instance_id: int | None = None) -> bytes:
    """A viewer's ``VIEWER_STATE``."""
    return _message(
        CommandTypeDC.VIEWER_STATE,
        state,
        instance_id=instance_id,
        target_group=TargetTypeDC.SERVER,
        client_type=TargetTypeDC.WEB,
    )


def read(data: bytes) -> MessageDC:
    return deserialize_root_message(data)


def payload_of(message: MessageDC) -> Any:
    """``payload_json`` parsed; None when it is missing or not JSON."""
    raw = message.payload_json
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None
