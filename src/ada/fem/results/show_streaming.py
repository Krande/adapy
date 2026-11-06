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
    from ada.visit.rendering.renderer_react import RenderBackend

ENV_FEA_BAKE_DIR = "ADA_FEA_SHOW_BAKE_DIR"
MANIFEST_NAME = "fea.manifest.json"
# Part of the cache key: bump when a change to the readers or the bake would make an
# existing cached bake wrong (2: Sesam 3-node beams no longer reordered; 3: a superelement
# assembly reads every superelement).
_CACHE_REVISION = 3


def bake_root() -> pathlib.Path:
    override = os.environ.get(ENV_FEA_BAKE_DIR)
    return pathlib.Path(override).expanduser() if override else pathlib.Path(tempfile.gettempdir()) / "ada-fea-show"


def bake_id_for(result: FEAResult, derived: tuple[str, ...] = ()) -> str:
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
            derived,
        )
    )
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def bake_for_show(result: FEAResult, derived=None) -> tuple[pathlib.Path, str]:
    """``(bake directory, bake id)`` of ``result``, baking it unless the cache already holds it.

    ``derived``: quantities worked out from the role fields (``ada.fem.results.derived``) baked beside the
    file's fields, for a result read case by case."""
    from ada.fem.results.artefacts import FEAResultStreamAdapter, bake_artefacts

    derived = tuple(str(d) for d in (derived or ()))
    lazy = getattr(result, "cases", None) is not None and not result.results
    if derived and not lazy:
        raise ValueError(
            f"{result.name}: derived fields ({', '.join(derived)}) are worked out from a result read case by case; "
            "read it with to_fem(..., lazy=True) or ada.from_fem_res(path, lazy=True)"
        )
    bake_id = bake_id_for(result, derived)
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
        if lazy:
            # A result read case by case (to_fem(..., lazy=True), from_fem_res(path, lazy=True)) holds no
            # fields: the bake reads its file, a case at a time, as the REST viewer's does, and the derived
            # quantities asked for beside them.
            from ada.fem.results.artefacts import make_stream_reader
            from ada.fem.results.derived import WithDerivedFields

            with make_stream_reader(pathlib.Path(result.results_file_path)) as reader:
                source = WithDerivedFields(reader, result, derived) if derived else reader
                bake_artefacts(source, work_dir, src=result.name or "fea")
        else:
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
    render_backend: RenderBackend | None = None,
    derived=None,
) -> None:
    """Bake ``result``, start the websocket server and a viewer if needed, and open the result in it.

    ``render_backend`` picks what draws in a viewer this opens (``"webgpu"``: the adacpp renderer); a
    viewer already connected keeps its own."""
    from ada.comms.wsock.client_sync import WebSocketClientSync
    from ada.visit.renderer_manager import RendererManager

    if not getattr(result, "executed", True):
        raise ValueError(f"{result.name}: the analysis has not run; result.execute() runs it")
    bake_dir, bake_id = bake_for_show(result, derived)
    manager = RendererManager(
        renderer="react",
        host=host,
        ws_port=port,
        server_exe=server_exe,
        server_args=server_args,
        run_ws_in_thread=run_ws_in_thread,
        render_backend=render_backend,
    )
    manager.start_server()
    with WebSocketClientSync(host, port) as wc:
        manager.ensure_liveness(wc)
        # Registered after the viewer is (being) opened: the server pushes the open to
        # every viewer connected now, and to one that connects later.
        wc.register_fea_source(result.name or "fea", str(bake_dir), bake_id=bake_id, open_in_viewer=True)
