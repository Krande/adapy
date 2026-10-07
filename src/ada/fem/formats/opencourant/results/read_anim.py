"""Native reader for OpenCourant / OpenRadioss animation files (``<root>A001``, ``<root>A002`` ...).

Each animation file is one output state of an explicit run: the (deformed) node
coordinates, the element connectivity and whatever nodal / element results the
engine deck requested with ``/ANIM/...``. The layout is a flat big-endian record
stream; it is ported from the MIT-licensed ``anim_to_vtk.cpp`` converter in the
OpenCourant Tools repository (output_converters/anim_to_vtk). No meshio, no
converter binary.

Optional trailing sections (time-history node lists, SPH) are not read: everything
this module returns sits before them in the file.
"""

from __future__ import annotations

import gzip
import pathlib
from dataclasses import dataclass, field

import numpy as np

#: Magic number of the "fast" animation format written by every current engine.
FASTMAGI10 = 0x542C
_SHORT2FLOAT = 3000.0


class AnimFormatError(ValueError):
    """The bytes are not a supported OpenRadioss animation file."""


@dataclass
class AnimElementBlock:
    """One element family of an animation state (2D facets, 3D bricks or 1D lines)."""

    connectivity: np.ndarray  # (n, k) 0-based node indices
    deleted: np.ndarray  # (n,) bool
    part_ends: np.ndarray  # (n_parts,) index one past the last element of each part
    part_names: list[str]
    scalars: dict[str, np.ndarray] = field(default_factory=dict)  # name -> (n,)
    tensors: dict[str, np.ndarray] = field(default_factory=dict)  # name -> (n, n_comp)
    element_ids: np.ndarray | None = None  # (n,) solver element ids

    @property
    def num_elements(self) -> int:
        return int(self.connectivity.shape[0])

    def part_index(self) -> np.ndarray:
        """Part number (0-based) of every element."""
        starts = np.concatenate([[0], self.part_ends[:-1]]) if len(self.part_ends) else np.zeros(0, int)
        out = np.zeros(self.num_elements, dtype=np.int32)
        for i, (s, e) in enumerate(zip(starts, self.part_ends)):
            out[int(s) : int(e)] = i
        return out


@dataclass
class AnimFrame:
    """One animation state."""

    time: float
    title: str
    coords: np.ndarray  # (n_nodes, 3) deformed coordinates at ``time``
    node_ids: np.ndarray | None
    nodal_scalars: dict[str, np.ndarray]  # name -> (n_nodes,)
    vectors: dict[str, np.ndarray]  # name -> (n_nodes, 3)
    shells: AnimElementBlock  # 4-node facets; triangles repeat their third node
    solids: AnimElementBlock | None = None  # 8-node bricks (degenerate for tets/pentas)
    lines: AnimElementBlock | None = None  # 2-node elements

    @property
    def num_nodes(self) -> int:
        return int(self.coords.shape[0])


class _Cursor:
    def __init__(self, data: bytes):
        self._b = memoryview(data)
        self._pos = 0

    def _take(self, nbytes: int) -> memoryview:
        end = self._pos + nbytes
        if end > len(self._b):
            raise AnimFormatError(f"truncated animation file: need {nbytes} bytes at offset {self._pos}")
        out = self._b[self._pos : end]
        self._pos = end
        return out

    def ints(self, n: int) -> np.ndarray:
        return np.frombuffer(self._take(4 * n), dtype=">i4").astype(np.int64)

    def int(self) -> int:
        return int(self.ints(1)[0])

    def floats(self, n: int) -> np.ndarray:
        return np.frombuffer(self._take(4 * n), dtype=">f4").astype(np.float32)

    def shorts(self, n: int) -> np.ndarray:
        return np.frombuffer(self._take(2 * n), dtype=">u2")

    def bytes_(self, n: int) -> np.ndarray:
        return np.frombuffer(self._take(n), dtype=np.uint8)

    def text(self, n: int) -> str:
        raw = bytes(self._take(n))
        return raw.split(b"\0", 1)[0].decode("latin-1").strip()

    def texts(self, count: int, width: int) -> list[str]:
        return [self.text(width) for _ in range(count)]


def _hierarchy(cur: _Cursor, n_parts: int) -> None:
    # subset / material / property index per part — not needed downstream
    cur.ints(n_parts)
    cur.ints(n_parts)
    cur.ints(n_parts)


def read_anim_bytes(data: bytes) -> AnimFrame:
    """Parse one animation state from raw (optionally gzip-compressed) bytes."""
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    cur = _Cursor(data)

    magic = cur.int()
    if magic != FASTMAGI10:
        raise AnimFormatError(f"unsupported animation magic 0x{magic:x} (expected 0x{FASTMAGI10:x})")

    time = float(cur.floats(1)[0])
    title = cur.text(81)
    cur.text(81)  # ModAnim text
    cur.text(81)  # RadiossRun text
    flags = cur.ints(10)
    has_mass, has_numbering, has_3d, has_1d, has_hierarchy = (bool(flags[i]) for i in range(5))

    # ---- 2D geometry (always present) -------------------------------------------------
    n_nodes = cur.int()
    n_facets = cur.int()
    n_parts = cur.int()
    n_func = cur.int()
    n_efunc = cur.int()
    n_vect = cur.int()
    n_tens = cur.int()
    n_skew = cur.int()
    if n_skew:
        cur.shorts(n_skew * 6)

    coords = cur.floats(3 * n_nodes).reshape(n_nodes, 3)

    conn = np.zeros((0, 4), dtype=np.int64)
    deleted = np.zeros(0, dtype=bool)
    if n_facets:
        conn = cur.ints(n_facets * 4).reshape(n_facets, 4)
        deleted = cur.bytes_(n_facets) != 0

    part_ends = np.zeros(0, dtype=np.int64)
    part_names: list[str] = []
    if n_parts:
        part_ends = cur.ints(n_parts)
        part_names = cur.texts(n_parts, 50)

    cur.shorts(3 * n_nodes)  # packed nodal normals (display only)

    nodal_scalars: dict[str, np.ndarray] = {}
    shell_scalars: dict[str, np.ndarray] = {}
    if n_func + n_efunc:
        names = cur.texts(n_func + n_efunc, 81)
        if n_func:
            vals = cur.floats(n_nodes * n_func).reshape(n_func, n_nodes)
            nodal_scalars = {names[i]: vals[i] for i in range(n_func)}
        if n_efunc:
            vals = cur.floats(n_facets * n_efunc).reshape(n_efunc, n_facets)
            shell_scalars = {names[n_func + i]: vals[i] for i in range(n_efunc)}

    vectors: dict[str, np.ndarray] = {}
    vec_names = cur.texts(n_vect, 81) if n_vect else []
    vec_vals = cur.floats(3 * n_nodes * n_vect).reshape(n_vect, n_nodes, 3) if n_vect else None
    for i, name in enumerate(vec_names):
        vectors[name] = vec_vals[i]

    shell_tensors: dict[str, np.ndarray] = {}
    if n_tens:
        names = cur.texts(n_tens, 81)
        vals = cur.floats(n_tens * n_facets * 3).reshape(n_tens, n_facets, 3)
        shell_tensors = {names[i]: vals[i] for i in range(n_tens)}

    if has_mass:
        cur.floats(n_facets)
        cur.floats(n_nodes)

    node_ids = None
    shell_ids = None
    if has_numbering:
        node_ids = cur.ints(n_nodes)
        shell_ids = cur.ints(n_facets)

    if has_hierarchy:
        _hierarchy(cur, n_parts)

    shells = AnimElementBlock(
        connectivity=conn,
        deleted=deleted,
        part_ends=part_ends,
        part_names=part_names,
        scalars=shell_scalars,
        tensors=shell_tensors,
        element_ids=shell_ids,
    )

    # ---- 3D geometry ------------------------------------------------------------------
    solids = None
    if has_3d:
        n_el = cur.int()
        n_p = cur.int()
        n_ef = cur.int()
        n_t = cur.int()
        s_conn = cur.ints(n_el * 8).reshape(n_el, 8)
        s_del = cur.bytes_(n_el) != 0
        s_ends = cur.ints(n_p)
        s_names = cur.texts(n_p, 50)
        s_scal: dict[str, np.ndarray] = {}
        if n_ef:
            names = cur.texts(n_ef, 81)
            vals = cur.floats(n_ef * n_el).reshape(n_ef, n_el)
            s_scal = {names[i]: vals[i] for i in range(n_ef)}
        s_tens: dict[str, np.ndarray] = {}
        if n_t:
            names = cur.texts(n_t, 81)
            vals = cur.floats(n_el * 6 * n_t).reshape(n_t, n_el, 6)
            s_tens = {names[i]: vals[i] for i in range(n_t)}
        if has_mass:
            cur.floats(n_el)
        s_ids = cur.ints(n_el) if has_numbering else None
        if has_hierarchy:
            _hierarchy(cur, n_p)
        solids = AnimElementBlock(s_conn, s_del, s_ends, s_names, s_scal, s_tens, s_ids)

    # ---- 1D geometry ------------------------------------------------------------------
    lines = None
    if has_1d:
        n_el = cur.int()
        n_p = cur.int()
        n_ef = cur.int()
        n_tors = cur.int()
        is_skew = cur.int()
        l_conn = cur.ints(n_el * 2).reshape(n_el, 2)
        l_del = cur.bytes_(n_el) != 0
        l_ends = cur.ints(n_p)
        l_names = cur.texts(n_p, 50)
        l_scal: dict[str, np.ndarray] = {}
        if n_ef:
            names = cur.texts(n_ef, 81)
            vals = cur.floats(n_el * n_ef).reshape(n_ef, n_el)
            l_scal = {names[i]: vals[i] for i in range(n_ef)}
        l_tens: dict[str, np.ndarray] = {}
        if n_tors:
            names = cur.texts(n_tors, 81)
            vals = cur.floats(n_el * 9 * n_tors).reshape(n_tors, n_el, 9)
            l_tens = {names[i]: vals[i] for i in range(n_tors)}
        if is_skew:
            cur.ints(n_el)
        if has_mass:
            cur.floats(n_el)
        l_ids = cur.ints(n_el) if has_numbering else None
        if has_hierarchy:
            _hierarchy(cur, n_p)
        lines = AnimElementBlock(l_conn, l_del, l_ends, l_names, l_scal, l_tens, l_ids)

    return AnimFrame(
        time=time,
        title=title,
        coords=coords,
        node_ids=node_ids,
        nodal_scalars=nodal_scalars,
        vectors=vectors,
        shells=shells,
        solids=solids,
        lines=lines,
    )


def read_anim_file(path: str | pathlib.Path) -> AnimFrame:
    """Parse one animation file from disk."""
    return read_anim_bytes(pathlib.Path(path).read_bytes())


def is_anim_frame_name(name: str) -> bool:
    """``<root>A001`` style names (optionally ``.gz``) — the engine's animation states."""
    stem = name[:-3] if name.endswith(".gz") else name
    return len(stem) > 4 and stem[-4] == "A" and stem[-3:].isdigit()


def find_anim_files(directory: str | pathlib.Path, root: str = None) -> list[pathlib.Path]:
    """All animation files of a run directory, in output order."""
    directory = pathlib.Path(directory)
    files = [p for p in directory.iterdir() if p.is_file() and is_anim_frame_name(p.name)]
    if root is not None:
        files = [p for p in files if p.name.startswith(f"{root}A")]
    return sorted(files, key=lambda p: p.name)
