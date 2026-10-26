"""CALL_KERNEL: the viewer's WebAssembly compute, answered by the server's Python process.

The browser viewer runs several adacpp functions as WebAssembly modules loaded from
``/wasm/`` (beam-solid expansion, load-case superposition, ...). The websocket viewer
opens from ``file://``, where there is no ``/wasm/`` to load them from -- but there is a
Python process on the other end of the socket with the same adacpp installed. The
frontend's ``compute`` capability sends those calls here instead; the result is the
same arrays the WebAssembly build returns.

Only the kernels in :data:`KERNELS` can be called: a request names one, it is never an
arbitrary function lookup.

Wire format: ``args_json`` is ``{"arrays": [descriptor, ...], ...scalars}`` and the
reply's ``result_json`` has the same shape. A descriptor is ``{"name", "dtype",
"offset", "count"}`` -- ``dtype`` one of :data:`DTYPES`, ``offset`` in bytes into the
message's ``data`` (8-byte aligned, so the browser can view it without copying) and
``count`` in elements.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Callable

import numpy as np

from ada.comms.fb_wrap_model_gen import (
    CommandTypeDC,
    KernelReplyDC,
    MessageDC,
    ServerReplyDC,
)
from ada.comms.fb_wrap_serializer import serialize_root_message
from ada.comms.msg_handling.on_error_reply import on_error_reply
from ada.comms.msg_handling.reply_to import reply_to
from ada.config import logger

if TYPE_CHECKING:
    from ada.comms.wsock.server import ConnectedClient, WebSocketAsyncServer

DTYPES = {"u8": np.uint8, "u32": np.uint32, "i32": np.int32, "f32": np.float32, "f64": np.float64}
_CODES = {np.dtype(v): k for k, v in DTYPES.items()}
_ALIGN = 8


class KernelCallError(ValueError):
    """A CALL_KERNEL request this server cannot run."""


def pack_arrays(arrays: dict[str, np.ndarray]) -> tuple[list[dict], bytes]:
    """``(descriptors, data)`` for ``arrays``: each one's bytes back to back, 8-byte aligned."""
    descriptors, chunks, offset = [], [], 0
    for name, arr in arrays.items():
        arr = np.ascontiguousarray(arr)
        code = _CODES.get(arr.dtype)
        if code is None:
            raise KernelCallError(f"array {name!r} has unsupported dtype {arr.dtype}")
        pad = (-offset) % _ALIGN
        if pad:
            chunks.append(b"\0" * pad)
            offset += pad
        raw = arr.tobytes(order="C")
        descriptors.append({"name": name, "dtype": code, "offset": offset, "count": int(arr.size)})
        chunks.append(raw)
        offset += len(raw)
    return descriptors, b"".join(chunks)


def unpack_arrays(descriptors: list[dict], data: bytes) -> dict[str, np.ndarray]:
    """The arrays ``descriptors`` place in ``data``, by name."""
    out = {}
    for d in descriptors or []:
        dtype = DTYPES.get(d.get("dtype"))
        if dtype is None:
            raise KernelCallError(f"array {d.get('name')!r} has unsupported dtype {d.get('dtype')!r}")
        offset, count = int(d["offset"]), int(d["count"])
        if offset < 0 or offset + count * np.dtype(dtype).itemsize > len(data):
            raise KernelCallError(f"array {d.get('name')!r} runs past the end of the data")
        out[d["name"]] = np.frombuffer(data, dtype=dtype, count=count, offset=offset)
    return out


def _require(inputs: dict[str, np.ndarray], *names: str) -> list[np.ndarray]:
    missing = [n for n in names if n not in inputs]
    if missing:
        raise KernelCallError(f"missing input array(s): {', '.join(missing)}")
    return [inputs[n] for n in names]


def _expand_beam_solids(args: dict, inputs: dict[str, np.ndarray]) -> tuple[dict, dict[str, np.ndarray]]:
    """The compact beam-solid artefact (AFBS bytes) and the main mesh's positions -> the swept
    beam mesh, exactly as the viewer's ``adacpp_extrude`` WebAssembly build returns it."""
    from ada.fem.results.artefacts.beam_compact import (
        expand_beam_solid_arrays,
        parse_beam_solids_compact,
    )

    compact, points = _require(inputs, "compact", "points")
    inst = parse_beam_solids_compact(compact.tobytes())
    return {}, expand_beam_solid_arrays(inst, points)


#: The kernels CALL_KERNEL may run: name -> ``fn(args, input arrays) -> (scalars, output arrays)``.
KERNELS: dict[str, Callable[[dict, dict[str, np.ndarray]], tuple[dict, dict[str, np.ndarray]]]] = {
    "expand_beam_solids": _expand_beam_solids,
}


def run_kernel(function: str, args_json: str, data: bytes) -> tuple[str, bytes]:
    """``(result_json, data)`` of kernel ``function`` called with ``args_json`` / ``data``."""
    kernel = KERNELS.get(function or "")
    if kernel is None:
        raise KernelCallError(f"no kernel {function!r}; this server offers {sorted(KERNELS)}")
    try:
        args = json.loads(args_json) if args_json else {}
    except json.JSONDecodeError as e:
        raise KernelCallError(f"args_json is not JSON: {e}") from None
    inputs = unpack_arrays(args.pop("arrays", []), data or b"")
    try:
        scalars, outputs = kernel(args, inputs)
    except ImportError as e:
        raise KernelCallError(
            f"kernel {function!r} needs adacpp, which this Python environment cannot import ({e})"
        ) from None
    descriptors, out_data = pack_arrays(outputs)
    return json.dumps({**scalars, "arrays": descriptors}), out_data


def call_kernel(server: WebSocketAsyncServer, client: ConnectedClient, message: MessageDC) -> None:
    payload = message.server.kernel_call if message.server is not None else None
    if payload is None or not payload.function:
        on_error_reply(
            server, client, error_message="CALL_KERNEL requires server.kernel_call.function", request_message=message
        )
        return
    try:
        result_json, data = run_kernel(payload.function, payload.args_json, payload.data)
    except KernelCallError as e:
        on_error_reply(server, client, error_message=str(e), request_message=message)
        return
    logger.debug(f"Kernel {payload.function!r} answered with {len(data)} bytes")

    reply = reply_to(
        message,
        instance_id=server.instance_id,
        command_type=CommandTypeDC.SERVER_REPLY,
        target_id=client.instance_id,
        target_group=client.group_type,
        server_reply=ServerReplyDC(
            reply_to=CommandTypeDC.CALL_KERNEL,
            kernel=KernelReplyDC(function=payload.function, result_json=result_json, data=data),
        ),
    )
    server.send_message_threadsafe(client, serialize_root_message(reply))
