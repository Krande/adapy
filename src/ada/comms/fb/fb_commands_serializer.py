from typing import Optional

import flatbuffers
from ada.comms.fb.commands import WebClient
from ada.comms.fb.fb_commands_gen import WebClientDC


def serialize_webclient(builder: flatbuffers.Builder, obj: Optional[WebClientDC]) -> Optional[int]:
    if obj is None:
        return None
    name_str = None
    if obj.name is not None:
        name_str = builder.CreateString(str(obj.name))
    address_str = None
    if obj.address is not None:
        address_str = builder.CreateString(str(obj.address))

    WebClient.Start(builder)
    if obj.instance_id is not None:
        WebClient.AddInstanceId(builder, obj.instance_id)
    if name_str is not None:
        WebClient.AddName(builder, name_str)
    if address_str is not None:
        WebClient.AddAddress(builder, address_str)
    if obj.port is not None:
        WebClient.AddPort(builder, obj.port)
    return WebClient.End(builder)
