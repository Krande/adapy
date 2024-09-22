import asyncio

from ada.comms.wsockets import WebSocketClientAsync


async def start_server():
    async with WebSocketClientAsync("localhost", 8765) as client:
        await client.check_server_liveness_using_fb()
        response = await client.recv()
        print(response)


if __name__ == '__main__':
    asyncio.run(start_server())
