import asyncio
import pathlib

from ada.comms.fb_deserializer import deserialize_root_message
from ada.comms.fb_model_gen import CommandTypeDC, FileObjectDC
from ada.comms.wsockets import WebSocketAsyncServer, ConnectedClient
from ada.config import logger


def on_message(server: WebSocketAsyncServer, client: ConnectedClient, message_data: bytes) -> None:
    message = deserialize_root_message(message_data)
    if message.command_type == CommandTypeDC.UPDATE_SCENE:
        logger.info(f"Received message from {client} to update scene")
        glb_file_data = message.file_object.filedata
        tmp_dir = pathlib.Path('temp')
        with open(tmp_dir / "scene.glb", "wb") as f:
            f.write(glb_file_data)

        file_object = FileObjectDC(
            filedata=glb_file_data,
            filepath=tmp_dir / "scene.glb",
            file_type=message.file_object.file_type,
            purpose=message.file_object.purpose
        )
        server.scene_meta.file_objects.append(file_object)
    elif message.command_type == CommandTypeDC.UPDATE_SERVER:
        logger.info(f"Received message from {client} to update server")
        logger.info(f"Message: {message}")
    elif message.command_type == CommandTypeDC.MESH_INFO_CALLBACK:
        logger.info(f"Received message from {client} to update mesh info")
        logger.info(f"Message: {message}")
    else:
        logger.error(f"Unknown command type: {message.command_type}")


async def start_async_server():
    server = WebSocketAsyncServer("localhost", 8765, on_message=on_message)
    await server.start_async()


if __name__ == '__main__':
    logger.setLevel("DEBUG")
    # start_ws_async_server()
    asyncio.run(start_async_server())
