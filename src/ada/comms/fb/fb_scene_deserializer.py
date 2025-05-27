from ada.comms.fb.fb_base_deserializer import deserialize_fileobject
from ada.comms.fb.fb_scene_gen import (
    CameraParamsDC,
    SceneDC,
    SceneOperationsDC,
    ScreenshotDC,
)


def deserialize_cameraparams(fb_obj) -> CameraParamsDC | None:
    if fb_obj is None:
        return None

    return CameraParamsDC(
        position=[fb_obj.Position(i) for i in range(fb_obj.PositionLength())] if fb_obj.PositionLength() > 0 else None,
        look_at=[fb_obj.LookAt(i) for i in range(fb_obj.LookAtLength())] if fb_obj.LookAtLength() > 0 else None,
        up=[fb_obj.Up(i) for i in range(fb_obj.UpLength())] if fb_obj.UpLength() > 0 else None,
        fov=fb_obj.Fov(),
        near=fb_obj.Near(),
        far=fb_obj.Far(),
        force_camera=fb_obj.ForceCamera(),
    )


def deserialize_screenshot(fb_obj) -> ScreenshotDC | None:
    if fb_obj is None:
        return None

    return ScreenshotDC(
        png_file_path=fb_obj.PngFilePath().decode("utf-8") if fb_obj.PngFilePath() is not None else None
    )


def deserialize_scene(fb_obj) -> SceneDC | None:
    if fb_obj is None:
        return None

    return SceneDC(
        operation=SceneOperationsDC(fb_obj.Operation()),
        camera_params=deserialize_cameraparams(fb_obj.CameraParams()),
        current_file=deserialize_fileobject(fb_obj.CurrentFile()),
    )
