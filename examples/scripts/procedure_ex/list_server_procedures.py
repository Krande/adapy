import asyncio
import pathlib

from ada.comms.fb_model_gen import ParameterDC, ProcedureStartDC
from ada.comms.wsock_client_async import WebSocketClientAsync

THIS_DIR = pathlib.Path(__file__).parent
ROOT_DIR = THIS_DIR.parent.parent


async def list_procedures():
    async with WebSocketClientAsync("localhost", 8765, "local") as ws_client:
        procedures = await ws_client.list_procedures()
        await ws_client.run_procedure(
            ProcedureStartDC("add_stiffeners", [ParameterDC(name="ifc_file", value="temp/MyBaseStructure.ifc")])
        )
        print(procedures)


if __name__ == "__main__":
    asyncio.run(list_procedures())
