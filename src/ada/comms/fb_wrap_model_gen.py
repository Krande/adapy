# This wraps the auto-generated FlatBuffers code so that I don't have to update changes to the FlatBuffer namespaces across the entire ada-py source code.
from ada.comms.fb.fb_base_gen import (
    ArrayTypeDC,
    ErrorDC,
    FileArgDC,
    FileObjectDC,
    FileObjectRefDC,
    FilePurposeDC,
    FileTypeDC,
    ParameterDC,
    ParameterTypeDC,
    ProcedureStartDC,
    ValueDC,
)
from ada.comms.fb.fb_commands_gen import CommandTypeDC, TargetTypeDC, WebClientDC
from ada.comms.fb.fb_meshes_gen import AppendMeshDC, MeshDC, MeshInfoDC
from ada.comms.fb.fb_procedures_gen import (
    ProcedureDC,
    ProcedureStateDC,
    ProcedureStoreDC,
)
from ada.comms.fb.fb_scene_gen import (
    CameraParamsDC,
    SceneDC,
    SceneOperationsDC,
    ScreenshotDC,
)
from ada.comms.fb.fb_server_gen import ServerDC, ServerReplyDC
from ada.comms.fb.fb_wsock_gen import MessageDC

__all__ = [
    "WebClientDC",
    "FileObjectDC",
    "FileObjectRefDC",
    "MeshInfoDC",
    "CameraParamsDC",
    "SceneDC",
    "ServerDC",
    "ProcedureStoreDC",
    "FileArgDC",
    "ProcedureDC",
    "ValueDC",
    "ParameterDC",
    "ProcedureStartDC",
    "ErrorDC",
    "ServerReplyDC",
    "ScreenshotDC",
    "MessageDC",
    "CommandTypeDC",
    "TargetTypeDC",
    "SceneOperationsDC",
    "FilePurposeDC",
    "FileTypeDC",
    "ProcedureStateDC",
    "ParameterTypeDC",
    "ArrayTypeDC",
    "MeshDC",
    "AppendMeshDC",
]
