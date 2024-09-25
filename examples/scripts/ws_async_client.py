import asyncio
import pathlib

import ada
from ada.comms.fb_model_gen import FileObjectDC, FilePurposeDC, FileTypeDC
from ada.comms.wsock_client_async import WebSocketClientAsync

THIS_DIR = pathlib.Path(__file__).parent
ROOT_DIR = THIS_DIR.parent.parent


async def update_scene():
    a = ada.from_genie_xml(ROOT_DIR / "files/fem_files/sesam/curved_plates.xml")
    a.show(add_ifc_backend=True, stream_from_ifc_store=True)


if __name__ == "__main__":
    asyncio.run(update_scene())
