from __future__ import annotations
from typing import Optional, List
from dataclasses import dataclass

from ada.comms.fb.fb_commands_gen import CommandTypeDC, TargetTypeDC, WebClientDC
from ada.comms.fb.fb_scene_gen import SceneDC, ScreenshotDC
from ada.comms.fb.fb_server_gen import ServerDC, ServerReplyDC
from ada.comms.fb.fb_meshes_gen import MeshInfoDC, AppendMeshDC
from ada.comms.fb.fb_procedures_gen import ProcedureStoreDC


@dataclass
class MessageDC:
    instance_id: int = None
    command_type: Optional[CommandTypeDC] = None
    scene: Optional[SceneDC] = None
    server: Optional[ServerDC] = None
    mesh_info: Optional[MeshInfoDC] = None
    target_group: Optional[TargetTypeDC] = None
    client_type: Optional[TargetTypeDC] = None
    target_id: int = None
    web_clients: Optional[List[WebClientDC]] = None
    procedure_store: Optional[ProcedureStoreDC] = None
    server_reply: Optional[ServerReplyDC] = None
    screenshot: Optional[ScreenshotDC] = None
    package: Optional[AppendMeshDC] = None
