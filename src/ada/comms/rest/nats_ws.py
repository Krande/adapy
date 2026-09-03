"""Make a server-closed WebSocket look like what nats-py already handles: EOF.

THE BUG THIS EXISTS FOR. ``nats-py``'s ``WebSocketTransport.readline`` special-
cases exactly one aiohttp message type::

    async def readline(self):
        data = await self._ws.receive()
        if data.type == aiohttp.WSMsgType.CLOSED:
            return b""
        return data.data

``CLOSED`` is the type aiohttp reports for a socket that is *already* closed. It
is not the type it reports when the peer closes one. A peer-initiated close
arrives as ``WSMsgType.CLOSE``, and aiohttp returns that message to the caller
with ``.data`` set to the **close code — an int**. So ``readline`` hands an int
to the protocol parser, which does ``bytearray.extend(data)``, and the read loop
dies on::

    TypeError: can't extend bytearray with int

``CLOSING`` (``.data is None``) and ``ERROR`` (``.data`` is an exception) fail
the same way for the same reason.

WHY THAT IS WORSE THAN A CRASH. ``Client._read_loop`` handles every *expected*
failure by calling ``_process_op_err``, which is what schedules a reconnect. Its
final catch-all does not::

    except Exception as ex:
        _logger.error("nats: encountered error", exc_info=ex)
        break

So the TypeError above breaks out of the read loop without ever entering the
reconnect path. The client is left with no reader, ``is_connected`` still true,
and no exception raised to anyone: it neither recovers nor fails. Every
subsequent request times out, and a long-lived process can sit like that
indefinitely while continuing to look healthy in its own logs.

WHAT THIS CHANGES, AND WHY IT IS ENOUGH. Reporting a close as ``b""`` is not a
workaround — it is the same signal the TCP transport gives, and the path
nats-py is already written for. An empty read leaves the parser buffer
untouched; the next loop iteration sees ``at_eof()`` (aiohttp has marked the
socket closed by then, as the CLOSE branch calls ``close()`` before returning),
raises ``UnexpectedEOF``, and goes through ``_process_op_err`` into a normal
reconnect. Nothing else about the transport changes.

WHO IS AFFECTED. Only clients using the WebSocket transport, which is what a
``ws://`` or ``wss://`` URL selects — the shape used when the bus is reached
through an HTTP ingress rather than a raw TCP port. A TCP client gets a clean
EOF from the OS and reconnects correctly today, which is why this can go
unnoticed for a long time: the same server restart that recovers everywhere else
permanently strands the WebSocket clients.

WHY A SUBCLASS AND NOT A PATCHED METHOD. Rebinding the name the client module
looks up leaves ``nats.aio.transport.WebSocketTransport`` itself untouched, so
anything else in the process that imports it directly is unaffected. If a later
``nats-py`` fixes this upstream the subclass becomes a redundant no-op rather
than a conflict, because it does the same thing.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Marks our subclass so :func:`install_websocket_close_fix` is idempotent.
#: Reconnects build a fresh transport from whatever the client module names, so
#: installing once per process is all that is needed — but ``connect`` may be
#: called many times in one process and must not stack patches.
_INSTALLED_FLAG = "_ada_close_is_eof"


def _build_transport_class():
    """Subclass the WebSocket transport, overriding only ``readline``.

    Imported lazily: ``aiohttp`` is an optional dependency that only a
    WebSocket URL requires, and this module must be importable without it.
    """
    import aiohttp
    from nats.aio.transport import WebSocketTransport

    # Every aiohttp message type that means "there is nothing more to read".
    # CLOSE is the one that matters in practice (a server shutting down or
    # rolling); CLOSING and ERROR are included because they carry a non-bytes
    # payload for the same reason and would fail in the same place.
    over = (
        aiohttp.WSMsgType.CLOSE,
        aiohttp.WSMsgType.CLOSING,
        aiohttp.WSMsgType.CLOSED,
        aiohttp.WSMsgType.ERROR,
    )

    class ClosingAwareWebSocketTransport(WebSocketTransport):
        """A WebSocket transport that reports every kind of close as EOF."""

        async def readline(self):
            msg = await self._ws.receive()
            if msg.type in over:
                # The one line that matters. Anything else here would be a
                # close code, ``None`` or an exception — never bytes.
                return b""
            # Deliberately unchanged for every other type, including TEXT:
            # narrowing what the transport accepts is a separate decision from
            # fixing how it reports a close, and not one this needs to make.
            return msg.data

    setattr(ClosingAwareWebSocketTransport, _INSTALLED_FLAG, True)
    return ClosingAwareWebSocketTransport


def install_websocket_close_fix() -> bool:
    """Install the transport above, returning whether it is in place.

    Idempotent and non-fatal. A ``False`` return means this environment cannot
    take the fix — no ``aiohttp``, or a ``nats-py`` whose internals have moved —
    and is logged rather than raised: the connection that follows may well work,
    and refusing to open it would turn a degraded reconnect into no service at
    all.
    """
    try:
        import nats.aio.client as nats_client
    except Exception:  # pragma: no cover - nats-py is a hard dependency here
        logger.debug("nats-ws: nats.aio.client unavailable; close-as-EOF fix not installed")
        return False

    existing = getattr(nats_client, "WebSocketTransport", None)
    if existing is None:
        # The client no longer resolves the transport through this name, so
        # rebinding it would silently do nothing. Say so rather than report
        # a fix that is not installed.
        logger.warning(
            "nats-ws: this nats-py does not expose WebSocketTransport on its client module; "
            "a server-closed WebSocket may not trigger a reconnect"
        )
        return False

    if getattr(existing, _INSTALLED_FLAG, False):
        return True

    try:
        nats_client.WebSocketTransport = _build_transport_class()
    except Exception:
        logger.warning(
            "nats-ws: could not install the close-as-EOF WebSocket transport; "
            "a server-closed WebSocket may not trigger a reconnect",
            exc_info=True,
        )
        return False

    logger.debug("nats-ws: WebSocket close now reported as EOF")
    return True
