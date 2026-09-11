"""``SAVE_PROCEDURAL_MODEL`` (ws/REST parity plan, step 3 --
docs/documents/ws_rest_parity.rst): the local viewer's save verb.

Driven with a stub server/client, like ``test_request_id.py``, so the
assertion is on the serialized reply that would hit the socket. ``base_dir``
is passed explicitly everywhere so these tests never touch a real
``procedural_model_dir()``."""

from __future__ import annotations

import json
import pathlib
import sys
from dataclasses import dataclass, field

import pytest

from ada.comms.fb_wrap_deserializer import deserialize_root_message
from ada.comms.fb_wrap_model_gen import (
    CommandTypeDC,
    MessageDC,
    ProceduralModelSaveDC,
    ServerDC,
    TargetTypeDC,
)
from ada.comms.fb_wrap_serializer import serialize_root_message
from ada.comms.msg_handling.save_procedural_model import (
    CONFLICT_ERROR_CODE,
    ProceduralModelIdError,
    _content_hash,
    _model_path,
    _validate_model_id,
    save_procedural_model,
)


class _Scene:
    def __init__(self):
        self.file_objects = []
        self.object_meta = {}


@dataclass
class _Client:
    instance_id: int = 5
    group_type: TargetTypeDC = TargetTypeDC.WEB


@dataclass
class _Server:
    instance_id: int = 99
    debug: bool = False
    scene: _Scene = field(default_factory=_Scene)
    sent: list = field(default_factory=list)

    def send_message_threadsafe(self, client, payload: bytes) -> None:
        self.sent.append(payload)


def _save_command(model_id: str, doc: dict, expected_content_hash: str = "", request_id: str = "r-1") -> MessageDC:
    return MessageDC(
        instance_id=5,
        command_type=CommandTypeDC.SAVE_PROCEDURAL_MODEL,
        target_group=TargetTypeDC.SERVER,
        client_type=TargetTypeDC.WEB,
        request_id=request_id,
        server=ServerDC(
            save_procedural_model=ProceduralModelSaveDC(
                model_id=model_id,
                doc_json=json.dumps(doc),
                expected_content_hash=expected_content_hash,
            )
        ),
    )


def _last_reply(server: _Server) -> MessageDC:
    assert len(server.sent) == 1, "expected exactly one reply"
    return deserialize_root_message(server.sent[-1])


def _save(server, client, model_id, doc, expected_content_hash="", base_dir=None, request_id="r-1"):
    save_procedural_model(
        server, client, _save_command(model_id, doc, expected_content_hash, request_id), base_dir=base_dir
    )
    return _last_reply(server)


def test_write_creates_the_file_and_replies_with_its_hash(tmp_path):
    server, client = _Server(), _Client()
    doc = {"spaces": [], "equipments": []}

    reply = _save(server, client, "hull-a", doc, base_dir=tmp_path)

    assert reply.command_type == CommandTypeDC.SERVER_REPLY
    assert reply.request_id == "r-1"
    save_reply = reply.server_reply.save_procedural_model
    assert save_reply.model_id == "hull-a"

    written = _model_path("hull-a", base_dir=tmp_path)
    assert written.exists()
    on_disk = written.read_bytes()
    assert json.loads(on_disk) == doc
    assert save_reply.content_hash == _content_hash(on_disk)
    # `base_dir` here is the resolved model directory itself (what
    # `procedural_model_dir()` returns in production, subdirectory already
    # applied) -- the handler writes directly into it.
    assert written.parent == tmp_path.resolve()


def test_rewrite_with_matching_expected_hash_succeeds_and_updates_content(tmp_path):
    server, client = _Server(), _Client()
    first = _save(server, client, "hull-a", {"spaces": [], "equipments": []}, base_dir=tmp_path)
    first_hash = first.server_reply.save_procedural_model.content_hash

    server.sent.clear()
    second_doc = {"spaces": [{"NAME": "S1"}], "equipments": []}
    second = _save(server, client, "hull-a", second_doc, expected_content_hash=first_hash, base_dir=tmp_path)

    assert second.command_type == CommandTypeDC.SERVER_REPLY
    second_hash = second.server_reply.save_procedural_model.content_hash
    assert second_hash != first_hash

    on_disk = _model_path("hull-a", base_dir=tmp_path).read_bytes()
    assert json.loads(on_disk) == second_doc
    assert _content_hash(on_disk) == second_hash


def test_mismatched_expected_hash_is_rejected_as_a_conflict_and_leaves_disk_untouched(tmp_path):
    server, client = _Server(), _Client()
    first = _save(server, client, "hull-a", {"spaces": [], "equipments": []}, base_dir=tmp_path)
    first_hash = first.server_reply.save_procedural_model.content_hash

    server.sent.clear()
    reply = _save(
        server,
        client,
        "hull-a",
        {"spaces": [{"NAME": "intruder"}], "equipments": []},
        expected_content_hash="not-the-real-hash",
        base_dir=tmp_path,
    )

    assert reply.command_type == CommandTypeDC.ERROR
    assert reply.server_reply.error.code == CONFLICT_ERROR_CODE
    assert "conflict" in reply.server_reply.error.message.lower()

    on_disk = _model_path("hull-a", base_dir=tmp_path).read_bytes()
    assert _content_hash(on_disk) == first_hash  # untouched


def test_expected_hash_against_a_model_that_was_never_saved_is_also_a_conflict(tmp_path):
    server, client = _Server(), _Client()
    reply = _save(server, client, "never-saved", {}, expected_content_hash="deadbeef", base_dir=tmp_path)

    assert reply.command_type == CommandTypeDC.ERROR
    assert reply.server_reply.error.code == CONFLICT_ERROR_CODE
    assert not _model_path("never-saved", base_dir=tmp_path).exists()


@pytest.mark.parametrize(
    "bad_model_id",
    [
        "",
        "..",
        "../evil",
        "a/b",
        "a\\b",
        "/etc/passwd",
        "C:\\evil",
        ".hidden",
        "-leading-dash",
        "has space",
        "null\x00byte",
    ],
)
def test_path_traversal_and_malformed_model_ids_are_rejected_before_touching_disk(tmp_path, bad_model_id):
    server, client = _Server(), _Client()
    reply = _save(server, client, bad_model_id, {"spaces": []}, base_dir=tmp_path)

    assert reply.command_type == CommandTypeDC.ERROR
    # Rejected as a malformed request, not as a revision conflict.
    assert reply.server_reply.error.code != CONFLICT_ERROR_CODE
    # Nothing written anywhere under base_dir.
    assert list(tmp_path.rglob("*.json")) == []

    with pytest.raises(ProceduralModelIdError):
        _validate_model_id(bad_model_id)


def test_missing_payload_is_a_generic_error_not_a_crash():
    server, client = _Server(), _Client()
    message = MessageDC(
        instance_id=5,
        command_type=CommandTypeDC.SAVE_PROCEDURAL_MODEL,
        target_group=TargetTypeDC.SERVER,
        client_type=TargetTypeDC.WEB,
        request_id="r-empty",
    )
    save_procedural_model(server, client, message)
    reply = _last_reply(server)
    assert reply.command_type == CommandTypeDC.ERROR
    assert reply.request_id == "r-empty"


def test_procedural_model_dir_honours_env_override(tmp_path, monkeypatch):
    from ada.comms.msg_handling.save_procedural_model import (
        ENV_PROCEDURAL_MODEL_DIR,
        procedural_model_dir,
    )

    monkeypatch.setenv(ENV_PROCEDURAL_MODEL_DIR, str(tmp_path))
    assert procedural_model_dir() == tmp_path.resolve() / ".ada_procedural_models"


def test_default_procedural_model_dir_falls_back_to_cwd_with_no_main_file(monkeypatch):
    from ada.comms.msg_handling.save_procedural_model import (
        default_procedural_model_dir,
    )

    class _FakeMain:
        pass  # no __file__ attribute, the shape of a REPL/notebook's __main__

    monkeypatch.setitem(sys.modules, "__main__", _FakeMain())
    assert default_procedural_model_dir() == pathlib.Path.cwd()


def test_default_on_message_routes_save_procedural_model(tmp_path, monkeypatch):
    from ada.comms.msg_handling.default_on_message import default_on_message
    from ada.comms.msg_handling.save_procedural_model import ENV_PROCEDURAL_MODEL_DIR

    monkeypatch.setenv(ENV_PROCEDURAL_MODEL_DIR, str(tmp_path))
    server, client = _Server(), _Client()
    command = _save_command("routed", {"spaces": []}, request_id="r-route")
    default_on_message(server, client, serialize_root_message(command))

    reply = _last_reply(server)
    assert reply.command_type == CommandTypeDC.SERVER_REPLY
    assert reply.request_id == "r-route"
    assert reply.server_reply.save_procedural_model.model_id == "routed"
