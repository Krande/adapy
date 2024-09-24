import pytest

from ada.comms.wsock_client import WebSocketClient


@pytest.mark.asyncio
async def test_local_liveness_check_w_flatbuffer(mock_web_client):
    async with WebSocketClient(mock_web_client.host, mock_web_client.port, "local") as ws_client:
        await ws_client.check_target_liveness()
