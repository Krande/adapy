from typing import Optional

import flatbuffers
from ada.comms.fb.fb_base_serializer import serialize_fileobject
from ada.comms.fb.fb_scene_gen import CameraParamsDC, SceneDC, ScreenshotDC
from ada.comms.fb.scene import CameraParams, Scene, Screenshot


def serialize_cameraparams(builder: flatbuffers.Builder, obj: Optional[CameraParamsDC]) -> Optional[int]:
    if obj is None:
        return None
    CameraParams.StartPositionVector(builder, len(obj.position))
    for item in reversed(obj.position):
        builder.PrependFloat32(item)
    position_vector = builder.EndVector()
    CameraParams.StartLookAtVector(builder, len(obj.look_at))
    for item in reversed(obj.look_at):
        builder.PrependFloat32(item)
    look_at_vector = builder.EndVector()
    CameraParams.StartUpVector(builder, len(obj.up))
    for item in reversed(obj.up):
        builder.PrependFloat32(item)
    up_vector = builder.EndVector()

    CameraParams.Start(builder)
    if obj.position is not None:
        CameraParams.AddPosition(builder, position_vector)
    if obj.look_at is not None:
        CameraParams.AddLookAt(builder, look_at_vector)
    if obj.up is not None:
        CameraParams.AddUp(builder, up_vector)
    if obj.fov is not None:
        CameraParams.AddFov(builder, obj.fov)
    if obj.near is not None:
        CameraParams.AddNear(builder, obj.near)
    if obj.far is not None:
        CameraParams.AddFar(builder, obj.far)
    if obj.force_camera is not None:
        CameraParams.AddForceCamera(builder, obj.force_camera)
    return CameraParams.End(builder)


def serialize_screenshot(builder: flatbuffers.Builder, obj: Optional[ScreenshotDC]) -> Optional[int]:
    if obj is None:
        return None
    png_file_path_str = None
    if obj.png_file_path is not None:
        png_file_path_str = builder.CreateString(str(obj.png_file_path))

    Screenshot.Start(builder)
    if png_file_path_str is not None:
        Screenshot.AddPngFilePath(builder, png_file_path_str)
    return Screenshot.End(builder)


def serialize_scene(builder: flatbuffers.Builder, obj: Optional[SceneDC]) -> Optional[int]:
    if obj is None:
        return None
    camera_params_obj = None
    if obj.camera_params is not None:
        camera_params_obj = serialize_cameraparams(builder, obj.camera_params)
    current_file_obj = None
    if obj.current_file is not None:
        current_file_obj = serialize_fileobject(builder, obj.current_file)

    Scene.Start(builder)
    if obj.operation is not None:
        Scene.AddOperation(builder, obj.operation.value)
    if obj.camera_params is not None:
        Scene.AddCameraParams(builder, camera_params_obj)
    if obj.current_file is not None:
        Scene.AddCurrentFile(builder, current_file_obj)
    return Scene.End(builder)
