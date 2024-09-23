import asyncio
import pathlib

import ada
from ada.comms.fb_model_gen import FileObjectDC, FileTypeDC, FilePurposeDC
from ada.comms.wsock_client import WebSocketClient

THIS_DIR = pathlib.Path(__file__).parent
ROOT_DIR = THIS_DIR.parent.parent


def update_scene():
    a = ada.from_genie_xml(ROOT_DIR / "files/fem_files/sesam/curved_plates.xml")
    a.ifc_store.sync()
    scene = a.to_trimesh_scene(stream_from_ifc=True, merge_meshes=True)
    ifc_file = pathlib.Path("temp/test.ifc")
    with WebSocketClient("localhost", 8765) as client:
        # await client.check_server_liveness_using_fb()
        client.update_scene(scene)
        a.to_ifc(ifc_file)
        client.update_file_server(FileObjectDC(FileTypeDC.IFC, FilePurposeDC.DESIGN, ifc_file))


if __name__ == '__main__':
    update_scene()
