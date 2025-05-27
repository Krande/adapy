from ada.comms.fb.fb_commands_gen import WebClientDC


def deserialize_webclient(fb_obj) -> WebClientDC | None:
    if fb_obj is None:
        return None

    return WebClientDC(
        instance_id=fb_obj.InstanceId(),
        name=fb_obj.Name().decode("utf-8") if fb_obj.Name() is not None else None,
        address=fb_obj.Address().decode("utf-8") if fb_obj.Address() is not None else None,
        port=fb_obj.Port(),
    )
