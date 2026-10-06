"""``.radanim``: one uploadable file holding the animation states of an OpenCourant run.

An explicit run writes one extension-less file per output state (``<root>A001``,
``<root>A002`` ...). The viewer pipeline dispatches on a single source file with a
suffix, so a run is packed into a zip archive named ``<root>.radanim`` that carries
the raw solver files unchanged (and the time-history file ``<root>T01`` when present).
Reading streams one state at a time out of the archive.
"""

from __future__ import annotations

import pathlib
import zipfile
from typing import Iterator

from .read_anim import AnimFrame, find_anim_files, is_anim_frame_name, read_anim_bytes

RADANIM_SUFFIX = ".radanim"


def pack_radanim(run_dir: str | pathlib.Path, root: str, out_path: str | pathlib.Path = None) -> pathlib.Path:
    """Pack ``<root>A###`` (+ ``<root>T01``) from ``run_dir`` into ``<root>.radanim``."""
    run_dir = pathlib.Path(run_dir)
    frames = find_anim_files(run_dir, root)
    if not frames:
        raise FileNotFoundError(f"no animation files {root}A### in {run_dir}")
    out_path = pathlib.Path(out_path) if out_path is not None else run_dir / f"{root}{RADANIM_SUFFIX}"
    extras = [p for p in (run_dir / f"{root}T01",) if p.is_file()]
    # Stored, not deflated: float32 states barely compress and stored members stream fast.
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_STORED) as zf:
        for p in frames + extras:
            zf.write(p, arcname=p.name)
    return out_path


def radanim_frame_names(zf: zipfile.ZipFile) -> list[str]:
    return sorted(n for n in zf.namelist() if is_anim_frame_name(pathlib.PurePosixPath(n).name))


def iter_radanim_frames(path: str | pathlib.Path) -> Iterator[AnimFrame]:
    with zipfile.ZipFile(path) as zf:
        for name in radanim_frame_names(zf):
            yield read_anim_bytes(zf.read(name))


def iter_run_frames(source: str | pathlib.Path, root: str = None) -> Iterator[AnimFrame]:
    """States from a ``.radanim`` archive, a run directory, or a single animation file."""
    source = pathlib.Path(source)
    if source.is_dir():
        for p in find_anim_files(source, root):
            yield read_anim_bytes(p.read_bytes())
    elif source.suffix.lower() == RADANIM_SUFFIX:
        yield from iter_radanim_frames(source)
    else:
        yield read_anim_bytes(source.read_bytes())


def read_opencourant_results(source: str | pathlib.Path, root: str = None):
    """Read a run (``.radanim``, run directory or single state) into an :class:`FEAResult`."""
    from .fea_result import fea_result_from_frames

    source = pathlib.Path(source)
    name = root or source.stem
    return fea_result_from_frames(iter_run_frames(source, root), name, results_file_path=source)


def make_radanim_stream_reader(path):
    """Streaming-bake factory for ``.radanim`` sources (registered in the artefact readers)."""
    from ada.fem.results.artefacts.stream_adapter import FEAResultStreamAdapter

    return FEAResultStreamAdapter(read_opencourant_results(path))
