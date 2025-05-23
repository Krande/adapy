# This wraps the auto-generated FlatBuffers code so that I don't have to update changes to the FlatBuffer namespaces across the entire ada-py source code.
from ada.comms.fb.fb_wsock_deserializer import deserialize_root_message

__all__ = [
    "deserialize_root_message",
]
