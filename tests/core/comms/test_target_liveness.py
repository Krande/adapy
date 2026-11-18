"""A liveness PING waits for the PONG: other messages arriving first (a broadcast to every client) are
passed over, not taken for "not alive"; with no PONG it answers False once its time is up."""

from __future__ import annotations

import asyncio
import time

from ada.comms.fb_wrap_model_gen import CommandTypeDC, MessageDC, TargetTypeDC
from ada.comms.fb_wrap_serializer import serialize_root_message
from ada.comms.wsock.client_async import WebSocketClientAsync
from ada.comms.wsock.client_sync import WebSocketClientSync


def _msg(command: CommandTypeDC) -> bytes:
    return serialize_root_message(
        MessageDC(instance_id=7, command_type=command, target_group=TargetTypeDC.LOCAL, target_id=0)
    )


class _AsyncSocket:
    """Answers each send with ``replies`` (in order), then nothing."""

    def __init__(self, replies: list[bytes]):
        self.replies = list(replies)
        self.sent: list[bytes] = []

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        if self.replies:
            return self.replies.pop(0)
        await asyncio.sleep(3600)


class _SyncSocket:
    def __init__(self, replies: list[bytes]):
        self.replies = list(replies)

    def send(self, data):
        pass

    def recv(self, timeout=None):
        if self.replies:
            return self.replies.pop(0)
        time.sleep(timeout)
        raise TimeoutError


def _async_client(replies) -> WebSocketClientAsync:
    client = WebSocketClientAsync("localhost", 1, "local")
    client.websocket = _AsyncSocket(replies)
    return client


def _sync_client(replies) -> WebSocketClientSync:
    client = WebSocketClientSync("localhost", 1, "local")
    client.websocket = _SyncSocket(replies)
    return client


BROADCAST = CommandTypeDC.UPDATE_SCENE


def test_async_liveness_passes_over_other_messages_to_the_pong():
    client = _async_client([_msg(BROADCAST), _msg(BROADCAST), _msg(CommandTypeDC.PONG)])
    assert asyncio.run(client.check_target_liveness(timeout=2)) is True


def test_async_liveness_is_false_without_a_pong_within_its_time():
    client = _async_client([_msg(BROADCAST)])
    started = time.monotonic()
    assert asyncio.run(client.check_target_liveness(timeout=0.3)) is False
    assert time.monotonic() - started < 2, "the timeout is the whole wait, not one per message"


def test_sync_liveness_passes_over_other_messages_to_the_pong():
    client = _sync_client([_msg(BROADCAST), _msg(CommandTypeDC.PONG)])
    assert client.check_target_liveness(timeout=2) is True


def test_sync_liveness_is_false_without_a_pong_within_its_time():
    client = _sync_client([_msg(BROADCAST)])
    assert client.check_target_liveness(timeout=0.3) is False
