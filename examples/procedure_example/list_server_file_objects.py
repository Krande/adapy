import asyncio
import pathlib

from ada.comms.fb_model_gen import (
    FileObjectDC,
    FilePurposeDC,
    FileTypeDC,
    ParameterDC,
    ProcedureStartDC,
)
from ada.comms.wsock_client_async import WebSocketClientAsync

THIS_DIR = pathlib.Path(__file__).parent
ROOT_DIR = THIS_DIR.parent.parent


async def list_procedures():
    async with WebSocketClientAsync("localhost", 8765, "local") as ws_client:
        await ws_client.update_file_server(
            FileObjectDC("test_file", FileTypeDC.IFC, FilePurposeDC.DESIGN, THIS_DIR / "temp/MyBaseStructure.ifc")
        )
        file_objects = await ws_client.list_server_file_objects()
        print(file_objects)


if __name__ == "__main__":
    asyncio.run(list_procedures())
