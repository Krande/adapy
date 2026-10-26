"""``FEAResult.show()`` as the REST viewer shows a result: mesh first, fields on request.

The older path baked every nodal step into one GLB as animations and pushed it
over the websocket in one message: element fields were not sent at all, and a
large model outgrew the server's message limit. This bakes the result once
(:func:`~ada.fem.results.artefacts.bake_artefacts`: manifest, mesh GLB, one blob
per field), registers the bake with the websocket server, and asks the viewer to
open it. The viewer then reads it with the same streaming loader it uses over
REST -- the manifest, the mesh, then one step of one field at a time (see
``ada.comms.msg_handling.fea_sources``).

Bakes are cached under the system temp directory, keyed by what they were made
from, so showing the same result file again reuses its bake.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import shutil
import tempfile
import uuid
from typing import TYPE_CHECKING

from ada.config import logger

if TYPE_CHECKING:
    from ada.fem.results.common import FEAResult

ENV_FEA_BAKE_DIR = "ADA_FEA_SHOW_BAKE_DIR"
MANIFEST_NAME = "fea.manifest.json"
# Part of the cache key: bump when a change to the readers or the bake would make an
# existing cached bake wrong (2: Sesam 3-node beams no longer reordered; 3: a superelement
# assembly reads every superelement).
_CACHE_REVISION = 3


def bake_root() -> pathlib.Path:
    override = os.environ.get(ENV_FEA_BAKE_DIR)
    return pathlib.Path(override).expanduser() if override else pathlib.Path(tempfile.gettempdir()) / "ada-fea-show"


def bake_id_for(result: FEAResult) -> str:
    """A key for ``result``'s bake: stable for a result read from an unchanged file, unique otherwise.

    A result with no file behind it (built or derived in memory, e.g. a comparison's
    difference) cannot be recognised again, so it always gets a fresh bake."""
    from ada.fem.results.artefacts.formats import FEA_BAKE_VERSION

    path = result.results_file_path
    if path is None or not pathlib.Path(path).is_file():
        return uuid.uuid4().hex[:16]
    stat = pathlib.Path(path).stat()
    fields = sorted({(f.name, int(f.step)) for f in result.results})
    # The mesh's size too: one file can be read more than one way (one superelement or all).
    n_nodes = len(result.mesh.nodes.identifiers)
    key = repr(
        (
            str(pathlib.Path(path).resolve()),
            stat.st_size,
            stat.st_mtime_ns,
            FEA_BAKE_VERSION,
            _CACHE_REVISION,
            n_nodes,
            fields,
        )
    )
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def bake_for_show(result: FEAResult) -> tuple[pathlib.Path, str]:
    """``(bake directory, bake id)`` of ``result``, baking it unless the cache already holds it."""
    from ada.fem.results.artefacts import FEAResultStreamAdapter, bake_artefacts

    bake_id = bake_id_for(result)
    out_dir = bake_root() / bake_id
    if (out_dir / MANIFEST_NAME).is_file():
        logger.info(f"Reusing the FEA bake of {result.name!r} in {out_dir}")
        return out_dir, bake_id

    # Baked aside and moved into place whole, so a bake that fails (or a second show()
    # baking the same result at the same time) never leaves a half-written bake that a
    # later show() would take for a finished one.
    work_dir = out_dir.with_name(f"{bake_id}.partial-{uuid.uuid4().hex[:8]}")
    work_dir.mkdir(parents=True)
    logger.info(f"Baking {result.name!r} for the viewer into {out_dir}")
    try:
        # The same bake the REST viewer gets. Its compact beam solids are expanded through
        # the viewer's compute capability -- over the websocket, by this Python process.
        bake_artefacts(FEAResultStreamAdapter(result), work_dir, src=result.name or "fea")
        if out_dir.exists():
            shutil.rmtree(out_dir)  # left over from an older, failed bake: it has no manifest
        work_dir.rename(out_dir)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    return out_dir, bake_id


def show_streaming(
    result: FEAResult,
    host: str = "localhost",
    port: int = 8765,
    server_exe: pathlib.Path = None,
    server_args: list[str] = None,
    run_ws_in_thread: bool = False,
) -> None:
    """Bake ``result``, start the websocket server and a viewer if needed, and open the result in it."""
    from ada.comms.wsock.client_sync import WebSocketClientSync
    from ada.visit.renderer_manager import RendererManager

    bake_dir, bake_id = bake_for_show(result)
    manager = RendererManager(
        renderer="react",
        host=host,
        ws_port=port,
        server_exe=server_exe,
        server_args=server_args,
        run_ws_in_thread=run_ws_in_thread,
    )
    manager.start_server()
    with WebSocketClientSync(host, port) as wc:
        manager.ensure_liveness(wc)
        # Registered after the viewer is (being) opened: the server pushes the open to
        # every viewer connected now, and to one that connects later.
        wc.register_fea_source(result.name or "fea", str(bake_dir), bake_id=bake_id, open_in_viewer=True)
