"""Every reply the websocket server sends in answer to a command echoes the
command's ``request_id`` (via ``reply_to``); unsolicited pushes carry none.

The handlers are driven with a stub server/client so the assertion is on the
serialized bytes that would hit the socket, not on an intermediate object."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from ada.comms.fb_wrap_deserializer import deserialize_root_message
from ada.comms.fb_wrap_model_gen import (
    CommandTypeDC,
    MeshInfoDC,
    MessageDC,
    TargetTypeDC,
)
from ada.comms.fb_wrap_serializer import serialize_root_message
from ada.comms.msg_handling.default_on_message import default_on_message
from ada.comms.msg_handling.get_server_info import get_server_info_func
from ada.comms.msg_handling.list_file_objects import list_file_objects
from ada.comms.msg_handling.list_web_clients import list_web_clients_func
from ada.comms.msg_handling.mesh_info_callback import mesh_info_callback
from ada.comms.msg_handling.on_error_reply import on_error_reply
from ada.comms.msg_handling.reply_to import reply_to


class _Scene:
    def __init__(self):
        self.file_objects = []
        self.object_meta = {}


@dataclass
class _Client:
    instance_id: int = 5
    group_type: TargetTypeDC = TargetTypeDC.WEB


@dataclass
class _Server:
    instance_id: int = 99
    debug: bool = False
    scene: _Scene = field(default_factory=_Scene)
    connected_web_clients: list = field(default_factory=list)
    sent: list = field(default_factory=list)

    def send_message_threadsafe(self, client, payload: bytes) -> None:
        self.sent.append(payload)


def _last_reply(server: _Server) -> MessageDC:
    assert len(server.sent) == 1, "expected exactly one reply"
    return deserialize_root_message(server.sent[0])


def _command(command_type: CommandTypeDC, request_id: str, **extra) -> MessageDC:
    return MessageDC(
        instance_id=5,
        command_type=command_type,
        target_group=TargetTypeDC.SERVER,
        client_type=TargetTypeDC.WEB,
        request_id=request_id,
        **extra,
    )


def test_reply_to_echoes_request_id_and_refuses_override():
    incoming = _command(CommandTypeDC.LIST_PROCEDURES, "r-1")
    reply = reply_to(incoming, instance_id=99, command_type=CommandTypeDC.SERVER_REPLY)
    assert reply.request_id == "r-1"
    assert reply.command_type == CommandTypeDC.SERVER_REPLY
    with pytest.raises(TypeError):
        reply_to(incoming, request_id="other")


def test_reply_to_without_message_or_with_empty_id_omits_the_field():
    # An unsolicited push (no message being answered) and a command from an
    # older client that never set the field both serialize without request_id.
    for reply in (reply_to(None, command_type=CommandTypeDC.PING), reply_to(MessageDC(request_id=""))):
        decoded = deserialize_root_message(serialize_root_message(reply))
        assert decoded.request_id is None


@pytest.mark.parametrize(
    "handler, command_type",
    [
        (list_file_objects, CommandTypeDC.LIST_FILE_OBJECTS),
        (get_server_info_func, CommandTypeDC.GET_SERVER_INFO),
        (list_web_clients_func, CommandTypeDC.LIST_WEB_CLIENTS),
    ],
)
def test_command_handlers_echo_request_id(handler, command_type):
    server, client = _Server(), _Client()
    handler(server, client, _command(command_type, "r-42"))
    assert _last_reply(server).request_id == "r-42"


def test_mesh_info_reply_echoes_request_id():
    server, client = _Server(), _Client()
    incoming = _command(
        CommandTypeDC.MESH_INFO_CALLBACK, "r-mesh", mesh_info=MeshInfoDC(object_name="", face_index=0, file_name="")
    )
    mesh_info_callback(server, client, incoming)
    reply = _last_reply(server)
    assert reply.command_type == CommandTypeDC.MESH_INFO_REPLY
    assert reply.request_id == "r-mesh"


def test_error_reply_echoes_request_id_of_failed_command():
    server, client = _Server(), _Client()
    incoming = _command(CommandTypeDC.LIST_PROCEDURES, "r-err")
    on_error_reply(server, client, error_message="boom", request_message=incoming)
    reply = _last_reply(server)
    assert reply.command_type == CommandTypeDC.ERROR
    assert reply.request_id == "r-err"

    # Without a parsed command there is nothing to echo.
    server.sent.clear()
    on_error_reply(server, client, error_message="unparseable")
    assert _last_reply(server).request_id is None


def test_default_on_message_unknown_command_error_echoes_request_id():
    server, client = _Server(), _Client()
    # PONG is never routed to a handler, so it takes the "unknown command" branch.
    default_on_message(server, client, serialize_root_message(_command(CommandTypeDC.PONG, "r-unknown")))
    reply = _last_reply(server)
    assert reply.command_type == CommandTypeDC.ERROR
    assert reply.request_id == "r-unknown"


def test_default_on_message_handler_exception_still_echoes_request_id():
    server, client = _Server(), _Client()
    # LIST_PROCEDURES needs a procedure_store the stub does not have; the
    # handler raises after the message parsed, and the error reply must still
    # settle the client's pending request.
    default_on_message(server, client, serialize_root_message(_command(CommandTypeDC.LIST_PROCEDURES, "r-exc")))
    reply = _last_reply(server)
    assert reply.command_type == CommandTypeDC.ERROR
    assert reply.request_id == "r-exc"
