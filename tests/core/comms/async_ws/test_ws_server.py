import pytest

from ada.comms.wsockets import WebSocketClientAsync


@pytest.mark.asyncio
async def test_local_client(mock_web_client):
    async with WebSocketClientAsync(mock_web_client.host, mock_web_client.port, "local") as ws_client:
        assert await ws_client.check_server_liveness_using_json() is True


@pytest.mark.asyncio
async def test_local_liveness_check_w_flatbuffer(mock_web_client):
    async with WebSocketClientAsync(mock_web_client.host, mock_web_client.port, "local") as ws_client:
        await ws_client.check_server_liveness_using_fb()
