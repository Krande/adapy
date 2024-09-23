import asyncio
import pathlib

import ada
from ada.comms.wsockets import WebSocketClientAsync

THIS_DIR = pathlib.Path(__file__).parent
ROOT_DIR = THIS_DIR.parent.parent


async def update_scene():
    a = ada.from_genie_xml(ROOT_DIR / "files/fem_files/sesam/curved_plates.xml")
    a.ifc_store.sync()
    scene = a.to_trimesh_scene(stream_from_ifc=True, merge_meshes=True)

    async with WebSocketClientAsync("localhost", 8765) as client:
        # await client.check_server_liveness_using_fb()
        await client.update_scene(scene)


if __name__ == '__main__':
    asyncio.run(update_scene())
