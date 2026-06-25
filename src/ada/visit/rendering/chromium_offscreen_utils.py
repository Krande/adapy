"""Render a GLB to PNG via the *real* embed in headless Chromium.

Why exist when `pygfx_offscreen_utils.glb_to_image` already produces a
PNG? Because the embed (`src/frontend/embed/index.ts`) is the source
of truth for what a user sees in the browser — camera framing, line /
mesh material choices, lighting, anti-aliasing, the lot. Reimplementing
that in pygfx is fiddly and silently drifts (e.g. the `applyCameraPreset`
margin-multiplier semantics aren't obvious from the field name alone).

So instead we drive a headless Chromium through Playwright, mount the
exact same `dist-embed/index.js` the hosted viewer consumes, and
screenshot the canvas. The output is then bit-identical to what the
deployed embed renders in the user's tab.

Usage::

    from ada.visit.rendering.chromium_offscreen_utils import (
        glb_to_image_via_browser,
    )
    img = glb_to_image_via_browser("beam.glb")
    img.save("beam.png")

CLI::

    python -m ada.visit.rendering.chromium_offscreen_utils \
        path/to/model.glb --out poster.png

Requires `playwright` + the chromium binary (already declared in the
`tests` pixi env).
"""

from __future__ import annotations

import argparse
import contextlib
import http.server
import io
import json
import logging
import socket
import socketserver
import threading
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)


_EMBED_BUNDLE = Path(__file__).resolve().parents[4] / "src" / "frontend" / "dist-embed" / "index.js"


_HTML_PAGE = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<title>adapy embed poster</title>
<style>
  /* `mountViewer` forces `position: relative` on the host element,
     which silently kills `inset: 0` (only `position: absolute|fixed|
     sticky` honour inset). The host then collapses to `minHeight:
     400px` (mountViewer's other default), and the canvas ends up
     640×400 inside a 640×480 viewport — wrong aspect ratio
     (1.6 vs 1.333), projection matrix off by ~20% horizontally,
     beam renders too small. Use explicit width/height so the host
     stays at viewport dimensions regardless of how mountViewer
     re-touches `position`. */
  html, body { margin: 0; padding: 0; background: #ffffff;
    width: 100%; height: 100%; }
  #host { width: 100%; height: 100%; }
</style>
</head>
<body>
<div id="host" class="ada-viewer-scope"></div>
<script>
  // The embed bundle leaks `process.env.NODE_ENV` and `process.emit`
  // — Vite was supposed to inline those at build time but didn't.
  // Stub the Node-only globals so the module evaluates in a browser.
  window.process = { env: { NODE_ENV: "production" }, emit: () => {} };
</script>
<script type="module">
  import { mountViewer } from "/embed/index.js";
  const params = new URLSearchParams(location.search);
  const preset = JSON.parse(params.get("preset") || "{}");
  // Default to the iso_3 preset paradoc registers per ThreeDData.
  // Field set + values match paradoc.camera.presets.CameraPreset so
  // the chromium poster is bit-equivalent to what the live viewer
  // renders when mountViewer loads the same GLB with the same preset
  // pulled from the bundle's presets.json.
  const camera = Object.assign(
    {
      name: "iso_3",
      azimuth_deg: -135,
      elevation_deg: 30,
      roll_deg: 0,
      target: "bbox_center",
      distance: "fit",
      fov_deg: 45,
      margin: 1.15,
    },
    preset,
  );
  window.__poster = { ready: false, error: null };
  (async () => {
    try {
      const res = await fetch("/model.glb");
      if (!res.ok) throw new Error("model fetch failed: " + res.status);
      const buf = new Uint8Array(await res.arrayBuffer());
      mountViewer(document.getElementById("host"), {
        modelBytes: buf,
        camera,
        showControls: false,
        onReady: () => { window.__poster.ready = true; },
        onError: (err) => { window.__poster.error = String(err); },
      });
    } catch (err) {
      window.__poster.error = String(err);
    }
  })();
</script>
</body>
</html>
"""


class _PosterHandler(http.server.BaseHTTPRequestHandler):
    """Tiny request router that serves the embed, GLB, and host HTML."""

    # Bound at construction time via factory.
    embed_path: Path = _EMBED_BUNDLE
    glb_bytes: bytes = b""

    def log_message(self, fmt, *args):  # noqa: D401
        # Silence the default stderr access log — playwright handles
        # whatever surface-level diagnostics we actually need.
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/" or self.path.startswith("/?"):
            self._send(self._html(), "text/html; charset=utf-8")
        elif self.path == "/embed/index.js":
            self._send(self.embed_path.read_bytes(), "text/javascript")
        elif self.path == "/model.glb":
            self._send(self.glb_bytes, "model/gltf-binary")
        else:
            self.send_error(404, "not found")

    def _html(self) -> bytes:
        return _HTML_PAGE.encode("utf-8")

    def _send(self, body: bytes, ctype: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def _free_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def _serve(embed_path: Path, glb_bytes: bytes):
    """Background-serve the embed page; yield (host, port)."""

    port = _free_port()

    handler_cls = type(
        "_BoundPosterHandler",
        (_PosterHandler,),
        {"embed_path": embed_path, "glb_bytes": glb_bytes},
    )

    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", port), handler_cls)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield "127.0.0.1", port
    finally:
        httpd.shutdown()
        httpd.server_close()


def glb_to_image_via_browser(
    glb_path: str | Path,
    *,
    preset: dict | None = None,
    size: tuple[int, int] = (640, 480),
    wait_timeout: float = 20.0,
    embed_bundle: Path | None = None,
) -> Image.Image:
    """Render ``glb_path`` to a PIL Image using the production embed.

    Parameters
    ----------
    glb_path
        Path to the GLB on disk.
    preset
        Optional override for the camera preset dict. Fields match the
        embed's ``CameraPreset`` interface (azimuth_deg, elevation_deg,
        roll_deg, distance, fov_deg, margin). When omitted, mountViewer
        receives the ``iso_3`` defaults paradoc registers for figures.
    size
        Viewport size in CSS pixels (also the output PNG size at the
        default device-pixel-ratio of 1).
    wait_timeout
        Seconds to wait for ``onReady`` (the embed's signal that the
        first render frame has produced a stable image).
    embed_bundle
        Path override for ``dist-embed/index.js``. Defaults to the
        adapy checkout that contains this file.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "playwright not installed — `pixi install -e tests` or add " "playwright to whatever env is calling this."
        ) from exc

    glb_path = Path(glb_path)
    if not glb_path.exists():
        raise FileNotFoundError(glb_path)

    embed = Path(embed_bundle) if embed_bundle else _EMBED_BUNDLE
    if not embed.exists():
        raise FileNotFoundError(
            f"embed bundle not found at {embed}; run `npm run build:embed` " f"in src/frontend first."
        )

    glb_bytes = glb_path.read_bytes()

    with _serve(embed, glb_bytes) as (host, port):
        url_base = f"http://{host}:{port}/"
        if preset is not None:
            url = f"{url_base}?preset={json.dumps(preset, separators=(',', ':'))}"
        else:
            url = url_base

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=[
                    "--use-gl=angle",
                    "--use-angle=swiftshader",
                    "--enable-webgl",
                    "--ignore-gpu-blocklist",
                ],
            )
            context = browser.new_context(
                viewport={"width": size[0], "height": size[1]},
                device_scale_factor=1,
            )
            page = context.new_page()
            console_log: list[str] = []
            page.on(
                "console",
                lambda msg: console_log.append(f"[{msg.type}] {msg.text}"),
            )
            page.on(
                "pageerror",
                lambda exc: console_log.append(f"[pageerror] {exc}"),
            )
            page.goto(url, wait_until="domcontentloaded")
            # Block until mountViewer's onReady fires (or onError).
            try:
                page.wait_for_function(
                    "window.__poster && (window.__poster.ready || window.__poster.error)",
                    timeout=int(wait_timeout * 1000),
                )
            except Exception:
                # Surface any captured console output before re-raising so
                # callers can diagnose what the embed actually said.
                if console_log:
                    logger.warning(
                        "chromium poster page log up to timeout:\n  %s",
                        "\n  ".join(console_log),
                    )
                browser.close()
                raise
            err = page.evaluate("window.__poster && window.__poster.error")
            if err:
                if console_log:
                    logger.warning(
                        "chromium poster console:\n  %s",
                        "\n  ".join(console_log),
                    )
                browser.close()
                raise RuntimeError(f"embed mount failed: {err}")
            # One extra animation frame so the first WebGL draw has
            # actually landed in the swap chain before we grab pixels.
            page.evaluate("new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
            png_bytes = page.screenshot(type="png", omit_background=False)
            browser.close()

    return Image.open(io.BytesIO(png_bytes)).convert("RGBA")


def _cli() -> None:
    p = argparse.ArgumentParser(
        description="Render a GLB to PNG via the adapy embed in headless Chromium.",
    )
    p.add_argument("glb", type=Path, help="Path to the GLB file.")
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output PNG path (default: <glb>.chromium.png).",
    )
    p.add_argument(
        "--width",
        type=int,
        default=640,
        help="Viewport width in CSS pixels (default 640).",
    )
    p.add_argument(
        "--height",
        type=int,
        default=480,
        help="Viewport height in CSS pixels (default 480).",
    )
    p.add_argument(
        "--preset",
        type=str,
        default=None,
        help="JSON dict overriding the camera preset, e.g. " '\'{"azimuth_deg": 45, "margin": 1.15}\'.',
    )
    args = p.parse_args()

    preset = json.loads(args.preset) if args.preset else None
    out = args.out or args.glb.with_suffix(".chromium.png")
    img = glb_to_image_via_browser(
        args.glb,
        size=(args.width, args.height),
        preset=preset,
    )
    img.save(str(out))
    print(f"wrote {out}")


if __name__ == "__main__":
    _cli()
