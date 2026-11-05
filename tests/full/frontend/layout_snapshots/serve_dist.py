"""Serve a built frontend dist on a port, proxying the API routes to a running viewer API.

Usage: python serve_dist.py <dist_dir> <port> [upstream=http://127.0.0.1:8080]
"""

import http.client
import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

PROXY_PREFIXES = ("/api/", "/api?", "/config.js", "/auth/", "/plugins/")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("application/wasm", ".wasm")


def make_handler(dist: Path, upstream):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            pass

        def _proxy(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            conn = http.client.HTTPConnection(upstream.hostname, upstream.port, timeout=120)
            headers = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "connection")}
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() in ("transfer-encoding", "connection", "content-length"):
                    continue
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
            conn.close()

        def _static(self):
            path = urlsplit(self.path).path
            target = (dist / path.lstrip("/")).resolve()
            if not target.is_relative_to(dist) or not target.is_file():
                target = dist / "index.html"
            data = target.read_bytes()
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _handle(self):
            if self.path.startswith(PROXY_PREFIXES) or self.path == "/api":
                try:
                    self._proxy()
                except Exception as exc:
                    self.send_error(502, str(exc))
            else:
                self._static()

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = _handle

    return Handler


def main():
    dist = Path(sys.argv[1]).resolve()
    port = int(sys.argv[2])
    upstream = urlsplit(sys.argv[3] if len(sys.argv) > 3 else "http://127.0.0.1:8080")
    print(f"serving {dist} on :{port} -> {upstream.geturl()}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), make_handler(dist, upstream)).serve_forever()


if __name__ == "__main__":
    main()
