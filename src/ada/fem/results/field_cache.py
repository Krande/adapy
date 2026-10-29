"""Field values kept in a temporary file instead of the process's memory.

:class:`FieldCache` writes each batch of fields' ``values`` to one file and swaps every array
for a copy-on-write memory map of what it wrote. The bytes, dtype, shape and memory order are
unchanged. The pages are file-backed: the OS reads them in on first touch and can drop them
again under memory pressure, so a result much larger than RAM stays usable. Writing to an
array changes the process's copy, never the file.

The file lasts as long as the arrays mapped from it. On POSIX it is unlinked as soon as it is
open; elsewhere it is removed when the cache is closed and the last mapped array is freed.
"""

from __future__ import annotations

import mmap
import os
import pathlib
import tempfile
import weakref
from typing import Iterable

import numpy as np

#: Where the cache files go when no directory is given: this, else the system temp directory.
CACHE_DIR_ENV = "ADA_FIELD_CACHE_DIR"


class FieldCache:
    def __init__(self, directory: str | os.PathLike | None = None):
        directory = directory or os.environ.get(CACHE_DIR_ENV) or None
        fd, name = tempfile.mkstemp(prefix="ada-fields-", suffix=".bin", dir=directory)
        self._file = os.fdopen(fd, "w+b", buffering=0)
        self.path = pathlib.Path(name)
        self._unlinked = _try_unlink(self.path) if os.name == "posix" else False
        self.nbytes = 0
        self._maps: list[weakref.ref] = []

    def spill(self, fields: Iterable) -> None:
        """Move each field's ``values`` to the file. Arrays of objects and empty arrays stay."""
        fields = [f for f in fields if _spillable(f.values)]
        arrays = list({id(f.values): f.values for f in fields}.values())
        if not arrays:
            return
        start = self._file.seek(0, os.SEEK_END)
        start += -start % mmap.ALLOCATIONGRANULARITY
        layout, size = {}, 0
        for v in arrays:
            size += -size % 64
            layout[id(v)] = size
            size += v.nbytes
        self._file.truncate(start + size)
        for v in arrays:
            self._file.seek(start + layout[id(v)])
            _write_all(self._file, _raw(v))
        _drop_from_page_cache(self._file, start, size)
        mapped = mmap.mmap(self._file.fileno(), size, access=mmap.ACCESS_COPY, offset=start)
        self._maps.append(weakref.ref(mapped))
        views = {}
        for v in arrays:
            fortran = v.flags.f_contiguous and not v.flags.c_contiguous
            views[id(v)] = np.ndarray(
                v.shape, v.dtype, buffer=mapped, offset=layout[id(v)], order="F" if fortran else "C"
            )
        for f in fields:
            f.values = views[id(f.values)]
        self.nbytes += size

    def close(self) -> None:
        """Close the file. The mapped arrays stay valid; the file goes with the last of them."""
        if self._file.closed:
            return
        self._file.close()
        if self._unlinked:
            return
        live = [m for m in (r() for r in self._maps) if m is not None]
        if not live:
            _try_unlink(self.path)
            return
        remaining = [len(live)]

        def last_one_freed():
            remaining[0] -= 1
            if remaining[0] == 0:
                _try_unlink(self.path)

        for m in live:
            weakref.finalize(m, last_one_freed)

    def __enter__(self) -> FieldCache:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _spillable(values) -> bool:
    return isinstance(values, np.ndarray) and values.dtype != object and values.nbytes > 0


def _raw(values: np.ndarray) -> np.ndarray:
    """The bytes of ``values`` in its memory order (C unless it is Fortran-ordered only)."""
    if values.flags.f_contiguous and not values.flags.c_contiguous:
        values = values.T
    return np.ascontiguousarray(values).reshape(-1).view(np.uint8)


def _write_all(file, data: np.ndarray) -> None:
    view, done = memoryview(data), 0
    while done < len(view):
        done += file.write(view[done:])


def _drop_from_page_cache(file, start: int, size: int) -> None:
    """Write the pages out and let the OS drop them: what was just written is read again
    only when a value is used, and holding it would push out what the reader still needs."""
    if not hasattr(os, "posix_fadvise"):
        return
    os.fdatasync(file.fileno())
    os.posix_fadvise(file.fileno(), start, size, os.POSIX_FADV_DONTNEED)


def _try_unlink(path: pathlib.Path) -> bool:
    try:
        path.unlink()
        return True
    except OSError:
        return False
