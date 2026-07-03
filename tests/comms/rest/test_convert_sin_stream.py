"""SIN range-stream conversion — ``convert(..., source_uri=...)``.

The worker skips the full download of a ``.sin`` deck and hands the child a
presigned GET URL instead; the SIN reader (``open_sin``) then range-fetches
pages of the deck straight from object storage. This exercises that plumbing
end-to-end against a local range-capable HTTP server: the streamed conversion
must be byte-identical to the plain local-file conversion, with ``src_path``
reduced to an empty suffix-only stub (exactly what the worker passes).

Fixture is the cantilever shell static analysis SIN (see
``tests/core/fem/formats/sesam/test_read_sin.py``).
"""

from __future__ import annotations

import pathlib
import re
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

SIN_PATH = pathlib.Path(__file__).resolve().parents[3] / (
    "files/fem_files/cantilever/sesam/static/shell/STATIC_SHELL_CANTILEVER_SESAMR1.SIN"
)


class _RangeHandler(BaseHTTPRequestHandler):
    """Minimal Range-GET file server (stdlib http.server has no Range support)."""

    def log_message(self, *args):  # noqa: D102 — silence per-request stderr noise
        pass

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", str(SIN_PATH.stat().st_size))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

    def do_GET(self):
        size = SIN_PATH.stat().st_size
        m = re.match(r"bytes=(\d+)-(\d+)?", self.headers.get("Range") or "")
        data = SIN_PATH.read_bytes()
        if m is None:
            self.send_response(200)
            self.send_header("Content-Length", str(size))
            self.end_headers()
            self.wfile.write(data)
            return
        start = int(m.group(1))
        end = min(int(m.group(2)) if m.group(2) else size - 1, size - 1)
        self.send_response(206)
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        self.wfile.write(data[start : end + 1])


@pytest.fixture
def range_server_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/{SIN_PATH.name}"
    server.shutdown()
    thread.join(timeout=5)


def test_sin_source_uri_streams_byte_identical(range_server_url):
    """convert() with source_uri range-fetches the deck; output matches the
    local-file conversion byte-for-byte and src_path stays an empty stub."""
    from ada.comms.rest.converter import convert, result_bytes

    key = f"fem/sin/{SIN_PATH.name}"
    local = result_bytes(convert(SIN_PATH, key, "glb"))

    stub = pathlib.Path(tempfile.mkstemp(suffix=SIN_PATH.suffix)[1])
    try:
        streamed = result_bytes(convert(stub, key, "glb", source_uri=range_server_url))
        assert stub.stat().st_size == 0
    finally:
        stub.unlink()

    assert streamed[:4] == b"glTF"
    assert streamed == local


def test_sin_source_uri_ignored_for_sif(tmp_path):
    """A handler that doesn't honour source_uri must still work when it's
    passed — the kwarg is forwarded to every handler for the (from, to) pair,
    and only the SIN branch consumes it."""
    from ada.comms.rest.converter import UnsupportedFormat, convert

    # .sin -> non-glb has no registration; source_uri must not change that.
    stub = tmp_path / "deck.SIN"
    stub.touch()
    with pytest.raises(UnsupportedFormat):
        convert(stub, "fem/sin/deck.SIN", "ifc", source_uri="http://127.0.0.1:1/never-hit")
