"""``LIST_PROCEDURAL_MODELS`` and ``LOAD_PROCEDURAL_MODEL`` (ws/REST parity plan, step 6 --
docs/documents/ws_rest_parity.rst): the local-disk model browser's read half of
``SAVE_PROCEDURAL_MODEL``.

Driven with the same stub server/client as ``test_save_procedural_model.py``, so the assertion is
on the serialized reply that would hit the socket. ``base_dir`` is passed explicitly everywhere so
these tests never touch a real ``procedural_model_dir()``."""

from __future__ import annotations

import json

from ada.comms.fb_wrap_deserializer import deserialize_root_message
from ada.comms.fb_wrap_model_gen import (
    CommandTypeDC,
    MessageDC,
    ProceduralModelLoadDC,
    ServerDC,
    TargetTypeDC,
)
from ada.comms.fb_wrap_serializer import serialize_root_message
from ada.comms.msg_handling.list_procedural_models import list_procedural_models
from ada.comms.msg_handling.load_procedural_model import load_procedural_model
from ada.comms.msg_handling.save_procedural_model import (
    CONFLICT_ERROR_CODE,
    _content_hash,
)

from .test_save_procedural_model import _Client, _save, _save_command, _Server


def _list_command(request_id: str = "r-list") -> MessageDC:
    return MessageDC(
        instance_id=5,
        command_type=CommandTypeDC.LIST_PROCEDURAL_MODELS,
        target_group=TargetTypeDC.SERVER,
        client_type=TargetTypeDC.WEB,
        request_id=request_id,
    )


def _load_command(model_id: str, request_id: str = "r-load") -> MessageDC:
    return MessageDC(
        instance_id=5,
        command_type=CommandTypeDC.LOAD_PROCEDURAL_MODEL,
        target_group=TargetTypeDC.SERVER,
        client_type=TargetTypeDC.WEB,
        request_id=request_id,
        server=ServerDC(load_procedural_model=ProceduralModelLoadDC(model_id=model_id)),
    )


def _last_reply(server: _Server) -> MessageDC:
    assert len(server.sent) == 1, "expected exactly one reply"
    return deserialize_root_message(server.sent[-1])


def _list(server, client, base_dir, request_id="r-list"):
    list_procedural_models(server, client, _list_command(request_id), base_dir=base_dir)
    return _last_reply(server)


def _load(server, client, model_id, base_dir, request_id="r-load"):
    load_procedural_model(server, client, _load_command(model_id, request_id), base_dir=base_dir)
    return _last_reply(server)


def test_list_on_an_empty_directory_returns_no_entries(tmp_path):
    server, client = _Server(), _Client()

    reply = _list(server, client, tmp_path)

    assert reply.command_type == CommandTypeDC.SERVER_REPLY
    assert reply.request_id == "r-list"
    entries = reply.server_reply.list_procedural_models.entries
    assert entries in (None, [])


def test_list_after_a_save_reports_the_saved_model(tmp_path):
    server, client = _Server(), _Client()
    doc = {"spaces": [], "equipments": []}
    saved = _save(server, client, "hull-a", doc, base_dir=tmp_path)
    expected_hash = saved.server_reply.save_procedural_model.content_hash

    server.sent.clear()
    reply = _list(server, client, tmp_path)

    entries = reply.server_reply.list_procedural_models.entries
    assert len(entries) == 1
    entry = entries[0]
    assert entry.model_id == "hull-a"
    assert entry.content_hash == expected_hash
    on_disk_bytes = len(json.dumps(doc).encode("utf-8"))
    assert entry.size_bytes == on_disk_bytes
    assert entry.modified_at is not None and entry.modified_at > 0


def test_list_reports_one_entry_per_saved_model(tmp_path):
    server, client = _Server(), _Client()
    _save(server, client, "hull-a", {"spaces": []}, base_dir=tmp_path)
    server.sent.clear()
    _save(server, client, "hull-b", {"spaces": []}, base_dir=tmp_path)

    server.sent.clear()
    reply = _list(server, client, tmp_path)

    model_ids = sorted(e.model_id for e in reply.server_reply.list_procedural_models.entries)
    assert model_ids == ["hull-a", "hull-b"]


def test_load_round_trips_exactly_what_save_wrote(tmp_path):
    server, client = _Server(), _Client()
    doc = {"spaces": [{"NAME": "Deck1"}], "equipments": []}
    saved = _save(server, client, "hull-a", doc, base_dir=tmp_path)
    expected_hash = saved.server_reply.save_procedural_model.content_hash

    server.sent.clear()
    reply = _load(server, client, "hull-a", base_dir=tmp_path)

    assert reply.command_type == CommandTypeDC.SERVER_REPLY
    assert reply.request_id == "r-load"
    loaded = reply.server_reply.load_procedural_model
    assert loaded.model_id == "hull-a"
    assert json.loads(loaded.doc_json) == doc
    assert loaded.content_hash == expected_hash
    assert loaded.content_hash == _content_hash(loaded.doc_json.encode("utf-8"))


def test_load_of_an_unknown_model_id_is_a_generic_error(tmp_path):
    server, client = _Server(), _Client()

    reply = _load(server, client, "never-saved", base_dir=tmp_path)

    assert reply.command_type == CommandTypeDC.ERROR
    assert reply.request_id == "r-load"
    # Not the save handler's revision-conflict code -- there is nothing to conflict with on a read.
    assert reply.server_reply.error.code != CONFLICT_ERROR_CODE


def test_load_rejects_a_path_traversal_model_id_before_touching_disk(tmp_path):
    server, client = _Server(), _Client()

    reply = _load(server, client, "../evil", base_dir=tmp_path)

    assert reply.command_type == CommandTypeDC.ERROR
    assert list(tmp_path.rglob("*.json")) == []


def test_default_on_message_routes_list_and_load(tmp_path, monkeypatch):
    from ada.comms.msg_handling.default_on_message import default_on_message
    from ada.comms.msg_handling.save_procedural_model import ENV_PROCEDURAL_MODEL_DIR

    monkeypatch.setenv(ENV_PROCEDURAL_MODEL_DIR, str(tmp_path))
    server, client = _Server(), _Client()

    default_on_message(server, client, serialize_root_message(_save_command("routed", {"spaces": []})))
    server.sent.clear()

    default_on_message(server, client, serialize_root_message(_list_command()))
    list_reply = _last_reply(server)
    assert list_reply.command_type == CommandTypeDC.SERVER_REPLY
    assert [e.model_id for e in list_reply.server_reply.list_procedural_models.entries] == ["routed"]

    server.sent.clear()
    default_on_message(server, client, serialize_root_message(_load_command("routed")))
    load_reply = _last_reply(server)
    assert load_reply.command_type == CommandTypeDC.SERVER_REPLY
    assert load_reply.server_reply.load_procedural_model.model_id == "routed"
