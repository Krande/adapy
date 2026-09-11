from __future__ import annotations

from typing import Any

from ada.comms.fb_wrap_model_gen import MessageDC


def reply_to(message: MessageDC | None, **fields: Any) -> MessageDC:
    """Build the reply to ``message``, echoing its ``request_id``.

    Every reply the websocket server sends in answer to a command is built here
    so the client can match it to the pending request that produced it. Pass
    ``None`` when the reply does not answer a specific command (or the command
    could not even be parsed); the field is then left unset, which is what an
    unsolicited push looks like on the wire. Callers never set ``request_id``
    themselves — it always comes from the message being answered.
    """
    if "request_id" in fields:
        raise TypeError("request_id is taken from the message being answered, not passed explicitly")
    request_id = message.request_id if message is not None else None
    # An empty string is what an older client (or the dataclass default) sends;
    # treat it as absent so the reply omits the field instead of echoing "".
    return MessageDC(request_id=request_id or None, **fields)
