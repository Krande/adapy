import pytest

from ada.visit.websocket_server import WebSocketClientAsync
from core.wsockets.conftest import HOST, PORT


@pytest.mark.asyncio
async def test_local_client(web_client):
    async with WebSocketClientAsync(HOST, PORT) as ws_client:
        await ws_client.send("ping")
        assert await ws_client.receive() == "pong"
