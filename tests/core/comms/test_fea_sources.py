"""``REGISTER_FEA_SOURCE``, ``GET_FEA_MANIFEST`` and ``GET_FEA_BLOB``: the server side of
``FEAResult.show()`` streaming a result to the viewer.

Driven with the stub server/client of ``test_save_procedural_model.py``, so the assertions are on
the serialized messages that would hit the socket."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ada.comms.fb_wrap_deserializer import deserialize_root_message
from ada.comms.fb_wrap_model_gen import (
    CommandTypeDC,
    FeaBlobRequestDC,
    FeaManifestRequestDC,
    FeaSourceRegisterDC,
    MessageDC,
    ServerDC,
    TargetTypeDC,
)
from ada.comms.msg_handling.fea_sources import (
    MANIFEST_NAME,
    get_fea_blob,
    get_fea_manifest,
    register_fea_source,
)

from .test_save_procedural_model import _Client, _Server

BLOB = bytes(range(64))


@dataclass
class _FeaServer(_Server):
    connected_web_clients: list = field(default_factory=list)
    fea_sources: dict = field(default_factory=dict)
    fea_open_source: object = None


def _bake(tmp_path):
    bake = tmp_path / "bake"
    bake.mkdir()
    (bake / MANIFEST_NAME).write_text(json.dumps({"fields": [], "mesh": {"url": "fea.mesh.glb"}}))
    (bake / "fea.mesh.glb").write_bytes(BLOB)
    (tmp_path / "secret.txt").write_text("not part of the bake")
    return bake


def _register(server, bake, open_in_viewer=True, source_key="cantilever"):
    command = MessageDC(
        instance_id=1,
        command_type=CommandTypeDC.REGISTER_FEA_SOURCE,
        target_group=TargetTypeDC.SERVER,
        client_type=TargetTypeDC.LOCAL,
        request_id="r-reg",
        server=ServerDC(
            register_fea_source=FeaSourceRegisterDC(
                source_key=source_key, bake_dir=str(bake), bake_id="b1", open_in_viewer=open_in_viewer
            )
        ),
    )
    register_fea_source(server, _Client(group_type=TargetTypeDC.LOCAL), command)


def _blob_command(filename, ranged=False, start=0, end=0, source_key="cantilever"):
    return MessageDC(
        instance_id=5,
        command_type=CommandTypeDC.GET_FEA_BLOB,
        target_group=TargetTypeDC.SERVER,
        client_type=TargetTypeDC.WEB,
        request_id="r-blob",
        server=ServerDC(
            fea_blob_request=FeaBlobRequestDC(
                source_key=source_key, filename=filename, ranged=ranged, start=start, end=end
            )
        ),
    )


def _replies(server):
    return [deserialize_root_message(m) for m in server.sent]


def test_register_answers_the_local_client_and_tells_every_viewer_to_open(tmp_path):
    viewers = [_Client(instance_id=7), _Client(instance_id=8)]
    server = _FeaServer(connected_web_clients=viewers)

    _register(server, _bake(tmp_path))

    reply, *pushes = _replies(server)
    assert reply.command_type == CommandTypeDC.SERVER_REPLY
    assert reply.request_id == "r-reg"
    assert reply.server_reply.fea_source.source_key == "cantilever"
    assert len(pushes) == len(viewers)
    for push in pushes:
        assert push.command_type == CommandTypeDC.OPEN_FEA_SOURCE
        assert push.server.open_fea_source.source_key == "cantilever"
        assert push.server.open_fea_source.bake_id == "b1"
    # Kept for a viewer that connects (or reloads) later.
    assert server.fea_open_source.source_key == "cantilever"


def test_register_without_open_pushes_nothing(tmp_path):
    server = _FeaServer(connected_web_clients=[_Client()])
    _register(server, _bake(tmp_path), open_in_viewer=False)
    assert len(server.sent) == 1
    assert server.fea_open_source is None


def test_register_refuses_a_directory_that_holds_no_bake(tmp_path):
    server = _FeaServer()
    _register(server, tmp_path)
    (reply,) = _replies(server)
    assert reply.command_type == CommandTypeDC.ERROR
    assert MANIFEST_NAME in reply.server_reply.error.message
    assert server.fea_sources == {}


def test_manifest_is_served_as_the_bake_wrote_it(tmp_path):
    server = _FeaServer()
    _register(server, _bake(tmp_path), open_in_viewer=False)
    server.sent.clear()

    command = MessageDC(
        instance_id=5,
        command_type=CommandTypeDC.GET_FEA_MANIFEST,
        target_group=TargetTypeDC.SERVER,
        client_type=TargetTypeDC.WEB,
        request_id="r-man",
        server=ServerDC(fea_manifest_request=FeaManifestRequestDC(source_key="cantilever")),
    )
    get_fea_manifest(server, _Client(), command)

    (reply,) = _replies(server)
    assert reply.request_id == "r-man"
    assert reply.server_reply.fea_manifest.bake_id == "b1"
    assert json.loads(reply.server_reply.fea_manifest.manifest_json)["mesh"]["url"] == "fea.mesh.glb"


def test_blob_whole_file_and_inclusive_range(tmp_path):
    server = _FeaServer()
    _register(server, _bake(tmp_path), open_in_viewer=False)
    server.sent.clear()

    get_fea_blob(server, _Client(), _blob_command("fea.mesh.glb"))
    get_fea_blob(server, _Client(), _blob_command("/fea.mesh.glb", ranged=True, start=10, end=19))
    get_fea_blob(server, _Client(), _blob_command("fea.mesh.glb", ranged=True, start=60, end=1000))

    whole, window, clamped = (r.server_reply.fea_blob for r in _replies(server))
    assert whole.data == BLOB and whole.ranged is False and whole.total_size == len(BLOB)
    assert window.data == BLOB[10:20] and window.ranged is True
    assert clamped.data == BLOB[60:], "a range past the end stops at the end of the file"


def test_blob_refuses_files_outside_the_bake_and_unknown_sources(tmp_path):
    server = _FeaServer()
    _register(server, _bake(tmp_path), open_in_viewer=False)
    server.sent.clear()

    get_fea_blob(server, _Client(), _blob_command("../secret.txt"))
    get_fea_blob(server, _Client(), _blob_command(str(tmp_path / "secret.txt")))
    get_fea_blob(server, _Client(), _blob_command("fea.mesh.glb", source_key="other"))
    get_fea_blob(server, _Client(), _blob_command("missing.bin"))

    replies = _replies(server)
    assert [r.command_type for r in replies] == [CommandTypeDC.ERROR] * 4
    assert all(r.request_id == "r-blob" for r in replies)
    assert "no FEA source" in replies[2].server_reply.error.message
