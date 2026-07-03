"""HttpRangeSource against a method-strict HTTP server.

A presigned S3 URL is signed for one HTTP method: SigV4 puts the method in
the canonical request, so a HEAD against a GET-presigned URL is rejected
(403 Forbidden) by AWS/Garage/MinIO alike. The size probe therefore must be
a ranged GET, never a HEAD — this server enforces that the same way the
object store does.
"""

import http.server
import threading

import pytest

from ada.fem.formats.sesam.results.byte_source import HttpRangeSource

PAYLOAD = bytes(range(256)) * 40  # 10240 bytes


class _GetPresignedHandler(http.server.BaseHTTPRequestHandler):
    """Serves PAYLOAD; 403s any non-GET, like a GET-presigned S3 URL."""

    ignore_range = False

    def do_HEAD(self):  # noqa: N802
        self.send_error(403, "Forbidden")

    def do_GET(self):  # noqa: N802
        rng = self.headers.get("Range")
        if rng is None or self.ignore_range:
            body = PAYLOAD
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        spec = rng.split("=", 1)[1]
        lo_s, _, hi_s = spec.partition("-")
        lo, hi = int(lo_s), min(int(hi_s), len(PAYLOAD) - 1)
        body = PAYLOAD[lo : hi + 1]
        self.send_response(206)
        self.send_header("Content-Range", f"bytes {lo}-{hi}/{len(PAYLOAD)}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # keep pytest output clean
        pass


@pytest.fixture()
def strict_server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _GetPresignedHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/deck.sin"
    srv.shutdown()
    srv.server_close()


def test_size_probe_survives_get_only_presign(strict_server):
    src = HttpRangeSource(strict_server)
    assert src.size() == len(PAYLOAD)
    assert src.fetch(0, 4) == PAYLOAD[:4]
    assert src.fetch(1000, 16) == PAYLOAD[1000:1016]


def test_size_probe_falls_back_to_content_length_on_200(strict_server):
    _GetPresignedHandler.ignore_range = True
    try:
        src = HttpRangeSource(strict_server)
        assert src.size() == len(PAYLOAD)
    finally:
        _GetPresignedHandler.ignore_range = False


def test_explicit_size_skips_probe():
    # No server at this port — constructing with size= must not touch it.
    src = HttpRangeSource("http://127.0.0.1:9/never.sin", size=123)
    assert src.size() == 123
