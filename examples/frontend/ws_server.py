import asyncio
import websockets

connected_clients = set()


async def register(websocket):
    connected_clients.add(websocket)


async def unregister(websocket):
    connected_clients.remove(websocket)


async def send_to_all_clients(message):
    if connected_clients:  # Check if there are any connected clients
        # Prepare a list to hold tasks
        tasks = []
        for client in connected_clients:
            if not client.closed:  # Check if the connection is still open
                task = asyncio.create_task(client.send(message))
                tasks.append(task)
            else:
                # Optionally, unregister the client if the connection is closed
                await unregister(client)
        # Wait for all tasks to complete
        if tasks:
            await asyncio.wait(tasks)


async def web_socket_server(websocket, path):
    """Receive a message from the client and forwards the message to all connected clients except the sender"""

    await register(websocket)
    try:
        async for message in websocket:
            # if the message is a blob, do not print it
            if not isinstance(message, bytes):
                print(f"Received message: {message}")
            await send_to_all_clients(message)
    finally:
        await unregister(websocket)


if __name__ == '__main__':
    start_server = websockets.serve(web_socket_server, "localhost", 8765)

    asyncio.get_event_loop().run_until_complete(start_server)
    asyncio.get_event_loop().run_forever()
