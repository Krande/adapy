"""``Message.request_id`` is the client-assigned correlation id appended to the
websocket envelope. A reply echoes the id of the command it answers so a client
can match it to a pending request; anything unsolicited leaves the field unset.
These tests pin the generated dataclass/serializer/deserializer round-trip so a
schema regeneration cannot silently drop the field."""

from ada.comms.fb.wsock import Message
from ada.comms.fb_wrap_deserializer import deserialize_root_message
from ada.comms.fb_wrap_model_gen import CommandTypeDC, MessageDC, TargetTypeDC
from ada.comms.fb_wrap_serializer import serialize_root_message


def test_request_id_round_trips_through_flatbuffer():
    message = MessageDC(
        instance_id=42,
        command_type=CommandTypeDC.LIST_PROCEDURES,
        target_group=TargetTypeDC.SERVER,
        client_type=TargetTypeDC.WEB,
        request_id="req-0001",
    )

    data = serialize_root_message(message)
    decoded = deserialize_root_message(data)

    assert decoded.request_id == "req-0001"
    assert decoded.instance_id == 42
    assert decoded.command_type == CommandTypeDC.LIST_PROCEDURES
    # The raw generated accessor sees the same bytes the TypeScript reader will.
    assert Message.Message.GetRootAsMessage(data, 0).RequestId() == b"req-0001"


def test_request_id_defaults_to_empty_and_stays_optional():
    # Older writers never set the field; a message without it must still parse
    # and read back as "no correlation" rather than crash the reader.
    message = MessageDC(instance_id=7, command_type=CommandTypeDC.PING)
    decoded = deserialize_root_message(serialize_root_message(message))
    assert not decoded.request_id

    # A buffer built without the field at all (as a pre-request_id client would)
    # reads back as absent through the raw accessor.
    import flatbuffers

    builder = flatbuffers.Builder(64)
    Message.Start(builder)
    Message.AddInstanceId(builder, 7)
    Message.AddCommandType(builder, CommandTypeDC.PING.value)
    builder.Finish(Message.End(builder))
    raw = bytes(builder.Output())
    assert Message.Message.GetRootAsMessage(raw, 0).RequestId() is None
    assert deserialize_root_message(raw).request_id is None
