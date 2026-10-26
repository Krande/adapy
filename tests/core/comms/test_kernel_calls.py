"""``CALL_KERNEL``: the viewer's WebAssembly compute, answered by the server's Python process.

The kernel must return exactly what the bake's own expansion returns for the same artefact --
the websocket viewer then builds the same beam mesh the REST viewer does."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import numpy as np
import pytest

from ada.comms.fb_wrap_deserializer import deserialize_root_message
from ada.comms.fb_wrap_model_gen import (
    CommandTypeDC,
    KernelCallDC,
    MessageDC,
    ServerDC,
    TargetTypeDC,
)
from ada.comms.msg_handling.kernel_calls import (
    KernelCallError,
    call_kernel,
    pack_arrays,
    run_kernel,
    unpack_arrays,
)

from ..fem.results.test_beam_compact import _mixed_beams
from .test_save_procedural_model import _Client, _Server


def test_arrays_round_trip_aligned_for_typed_array_views():
    arrays = {
        "a": np.arange(3, dtype=np.uint8),  # 3 bytes: the next array must start on an 8-byte boundary
        "b": np.array([1.5, -2.25], dtype=np.float64),
        "c": np.array([7, 8, 9], dtype=np.uint32),
    }
    descriptors, data = pack_arrays(arrays)

    assert all(d["offset"] % 8 == 0 for d in descriptors)
    back = unpack_arrays(descriptors, data)
    for name, arr in arrays.items():
        assert back[name].dtype == arr.dtype and np.array_equal(back[name], arr)


def test_only_whitelisted_kernels_run():
    with pytest.raises(KernelCallError, match="no kernel 'os.system'"):
        run_kernel("os.system", "{}", b"")


def test_a_descriptor_past_the_data_is_refused():
    with pytest.raises(KernelCallError, match="past the end"):
        unpack_arrays([{"name": "x", "dtype": "f64", "offset": 0, "count": 10}], b"\0" * 8)


def test_expand_beam_solids_matches_the_bakes_own_expansion(tmp_path):
    pytest.importorskip("adacpp")
    from ada.fem.results.artefacts.beam_compact import (
        collect_beam_solid_instances,
        expand_beam_solid_arrays,
        read_beam_solids_compact,
        write_beam_solids_compact,
    )

    beams, points = _mixed_beams()
    path = tmp_path / "fea.beam_solids.compact.bin"
    write_beam_solids_compact(collect_beam_solid_instances(beams, total_beams=len(beams)), path)
    want = expand_beam_solid_arrays(read_beam_solids_compact(path), points.astype(np.float32))

    # As the viewer sends it: the artefact's bytes and the main mesh's float32 positions.
    descriptors, data = pack_arrays(
        {"compact": np.frombuffer(path.read_bytes(), dtype=np.uint8), "points": points.astype(np.float32).ravel()}
    )
    result_json, out = run_kernel("expand_beam_solids", json.dumps({"arrays": descriptors}), data)
    got = unpack_arrays(json.loads(result_json)["arrays"], out)

    assert set(got) == set(want)
    for name in want:
        assert got[name].dtype == want[name].dtype, name
        assert np.array_equal(got[name], want[name]), name


@dataclass
class _KernelServer(_Server):
    connected_web_clients: list = field(default_factory=list)


def test_handler_replies_with_the_kernels_arrays_or_an_error():
    server = _KernelServer()
    descriptors, data = pack_arrays({"compact": np.zeros(4, dtype=np.uint8)})

    def command(function):
        return MessageDC(
            instance_id=5,
            command_type=CommandTypeDC.CALL_KERNEL,
            target_group=TargetTypeDC.SERVER,
            client_type=TargetTypeDC.WEB,
            request_id="r-k",
            server=ServerDC(
                kernel_call=KernelCallDC(function=function, args_json=json.dumps({"arrays": descriptors}), data=data)
            ),
        )

    call_kernel(server, _Client(), command("nope"))
    # Missing the "points" input: the kernel refuses rather than guessing.
    call_kernel(server, _Client(), command("expand_beam_solids"))

    unknown, missing = (deserialize_root_message(m) for m in server.sent)
    assert unknown.command_type == CommandTypeDC.ERROR and "no kernel 'nope'" in unknown.server_reply.error.message
    assert missing.command_type == CommandTypeDC.ERROR and "points" in missing.server_reply.error.message
    assert unknown.request_id == missing.request_id == "r-k"
