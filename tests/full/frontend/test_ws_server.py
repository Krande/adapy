import pytest

from ada.comms.wsock.client_async import WebSocketClientAsync


@pytest.mark.asyncio
async def test_list_procedures(ws_server):
    async with WebSocketClientAsync(ws_server.host, ws_server.port, "local") as ws_client:
        procedures = await ws_client.list_procedures()
        assert procedures is None
