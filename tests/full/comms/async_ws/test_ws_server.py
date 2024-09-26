import pytest

from ada.comms.wsock_client_async import WebSocketClientAsync


@pytest.mark.asyncio
async def test_local_liveness_check_w_flatbuffer(ws_server, mock_async_web_client):
    async with WebSocketClientAsync(mock_async_web_client.host, mock_async_web_client.port, "local") as ws_client:
        await ws_client.check_target_liveness()
