import pytest

from ada.comms.wsock.client_async import WebSocketClientAsync


@pytest.mark.asyncio
async def test_mock_web_client_liveness(ws_server, mock_async_web_client):
    async with WebSocketClientAsync(ws_server.host, ws_server.port, "local") as ws_client:
        await ws_client.check_target_liveness()
