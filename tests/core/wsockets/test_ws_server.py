import pytest

from ada.comms.wsockets import WebSocketClientAsync


@pytest.mark.asyncio
async def test_local_client(mock_host, mock_port, web_client):
    async with WebSocketClientAsync(mock_host, mock_port, "local") as ws_client:
        await ws_client.send("ping")
        msg = await ws_client.receive()
        assert msg["message"]  == "pong"
