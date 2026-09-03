"""A server-closed WebSocket has to look like EOF, or the client never reconnects.

``nats-py``'s WebSocket transport recognises one aiohttp close type, ``CLOSED``,
which is what aiohttp reports for a socket that is *already* closed. A peer
closing one is reported as ``CLOSE``, and the message aiohttp hands back carries
the close **code** in ``.data`` — an int. ``CLOSING`` carries ``None`` and
``ERROR`` carries an exception.

Passing any of those through reaches ``bytearray.extend(...)`` in the protocol
parser and raises ``TypeError``, which the read loop catches in a final
catch-all that logs and breaks WITHOUT going through the reconnect path every
other branch uses. The connection is then neither live nor failed: no reader,
``is_connected`` still true, every later request timing out, and nothing raised
to anyone.

So these tests are about one property — a close, of any flavour, produces
``b""`` — and one consequence, which is that the alternative is not "slightly
wrong" but fatal.
"""

import asyncio

import pytest

aiohttp = pytest.importorskip("aiohttp", reason="the WebSocket transport is an aiohttp extra")

from ada.comms.rest.nats_ws import (  # noqa: E402
    _build_transport_class,
    install_websocket_close_fix,
)


class _FakeWebSocket:
    """The one method the transport calls, handing back a scripted message."""

    def __init__(self, message):
        self._message = message

    async def receive(self):
        return self._message


def _readline(message):
    """Run the patched ``readline`` against a single scripted ws message.

    Built with ``__new__`` deliberately: the real ``__init__`` opens an
    ``aiohttp.ClientSession``, and nothing here is testing construction.
    """
    cls = _build_transport_class()
    transport = cls.__new__(cls)
    transport._ws = _FakeWebSocket(message)
    return asyncio.run(transport.readline())


def test_a_peer_close_reads_as_eof_not_as_its_close_code():
    # The case that breaks in the wild: a server restarting or being rolled.
    # aiohttp sets .data to the close code, so the unguarded path returns 1001.
    msg = aiohttp.WSMessage(aiohttp.WSMsgType.CLOSE, 1001, "going away")
    assert _readline(msg) == b""


def test_the_close_code_would_be_fatal_if_passed_through():
    # Why the test above matters, without reaching into the library: this is
    # exactly what the protocol parser does with whatever readline returns.
    buf = bytearray()
    with pytest.raises(TypeError):
        buf.extend(1001)


@pytest.mark.parametrize(
    "msg_type, data",
    [
        (aiohttp.WSMsgType.CLOSING, None),
        (aiohttp.WSMsgType.CLOSED, None),
        (aiohttp.WSMsgType.ERROR, RuntimeError("boom")),
    ],
    ids=["closing", "closed", "error"],
)
def test_every_other_end_of_stream_reads_as_eof_too(msg_type, data):
    # None and an exception are no more extendable into a bytearray than an int
    # is. CLOSED already worked; it is here so a future edit cannot lose it.
    assert _readline(aiohttp.WSMessage(msg_type, data, None)) == b""


def test_a_real_frame_is_still_passed_through_untouched():
    # The fix must not become a filter. Narrowing what the transport accepts is
    # a different decision from fixing how it reports a close.
    payload = b"PING\r\n"
    assert _readline(aiohttp.WSMessage(aiohttp.WSMsgType.BINARY, payload, None)) == payload


def test_installing_rebinds_the_name_the_client_resolves():
    import nats.aio.client as nats_client
    import nats.aio.transport as nats_transport

    original = nats_client.WebSocketTransport
    try:
        assert install_websocket_close_fix() is True
        patched = nats_client.WebSocketTransport
        # The client builds a fresh transport on every reconnect, so patching
        # the name it looks up is what makes the fix survive one.
        assert patched is not nats_transport.WebSocketTransport
        assert issubclass(patched, nats_transport.WebSocketTransport)

        # Idempotent: connect() may be called many times in one process, and
        # each call must not wrap the previous subclass in another.
        assert install_websocket_close_fix() is True
        assert nats_client.WebSocketTransport is patched
    finally:
        nats_client.WebSocketTransport = original


def test_the_library_module_itself_is_left_alone():
    # Anything else in the process that imports the transport directly keeps
    # the stock class. The patch is scoped to how the client resolves it.
    import nats.aio.client as nats_client
    import nats.aio.transport as nats_transport

    original = nats_client.WebSocketTransport
    stock = nats_transport.WebSocketTransport
    try:
        install_websocket_close_fix()
        assert nats_transport.WebSocketTransport is stock
    finally:
        nats_client.WebSocketTransport = original


def test_an_environment_that_cannot_take_the_fix_says_so_instead_of_raising(monkeypatch):
    # A nats-py that no longer resolves the transport through this name would
    # make the rebind silently do nothing. Better to report it un-installed and
    # still open the connection: a degraded reconnect beats no service.
    import nats.aio.client as nats_client

    monkeypatch.delattr(nats_client, "WebSocketTransport", raising=True)
    assert install_websocket_close_fix() is False
