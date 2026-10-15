"""Native reader for OpenCourant / OpenRadioss time-history files (``<root>T01``).

The engine writes the time histories its ``/TH/...`` cards request into one file of
big-endian Fortran sequential records (4-byte length markers around each record): a
header that describes the model hierarchy and every time-history group, then per output
time a record with the time, one with the global variables (energies, momentum, mass,
time step ...), the part and subset variables, and one record per group with the values
of each of its objects. The layout is ported from the MIT-licensed ``th_to_csv`` converter
in the OpenCourant Tools repository (output_converters/th_to_csv). The file stores the
variables' internal numbers, not their names; :data:`MONV_VARIABLE_NAMES` names those of a
monitored-volume group.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field

import numpy as np


class THFormatError(ValueError):
    """The bytes are not a supported OpenRadioss time-history file."""


#: Global variables in file order (``th_to_csv``); files written by newer engines carry more, unnamed.
GLOBAL_VARIABLE_NAMES = (
    "INTERNAL ENERGY",
    "KINETIC ENERGY",
    "X-MOMENTUM",
    "Y-MOMENTUM",
    "Z-MOMENTUM",
    "MASS",
    "TIME STEP",
    "ROTATION ENERGY",
    "EXTERNAL WORK",
    "SPRING ENERGY",
    "CONTACT ENERGY",
    "HOURGLASS ENERGY",
    "ELASTIC CONTACT ENERGY",
    "FRICTIONAL CONTACT ENERGY",
    "DAMPING CONTACT ENERGY",
    "PLASTIC WORK",
    "ADDED MASS",
    "PERCENTAGE ADDED MASS",
    "INLET MASS",
    "OUTLET MASS",
    "INLET ENERGY",
    "OUTLET ENERGY",
)

#: Internal numbers of a monitored volume's (``/TH/MONV``) variables: gas mass, volume, absolute pressure, surface
#: area, temperature, open vent area and vent outflow velocity.
MONV_VARIABLE_NAMES = {1: "MASS", 2: "VOL", 3: "P", 4: "A", 5: "T", 6: "AO", 7: "UO"}


@dataclass
class THGroup:
    """One time-history group: the objects it follows and their variables over time."""

    group_id: int
    type_code: int
    title: str
    object_ids: list[int]
    object_titles: list[str]
    variable_ids: list[int]
    values: np.ndarray = field(default=None)  # (n_times, n_objects, n_variables)

    def variable_names(self, names: dict[int, str] | None = None) -> list[str]:
        """Names of the variables from ``names`` (internal number -> name), ``VAR<n>`` where unknown."""
        names = names or {}
        return [names.get(v, f"VAR{v}") for v in self.variable_ids]

    def series(self, variable_id: int, object_index: int = 0) -> np.ndarray:
        """One variable of one object over time."""
        return self.values[:, object_index, self.variable_ids.index(variable_id)]


@dataclass
class TimeHistory:
    """The contents of a ``T01`` file."""

    times: np.ndarray  # (n_times,)
    global_values: np.ndarray  # (n_times, n_global)
    groups: list[THGroup]

    @property
    def global_names(self) -> list[str]:
        n = self.global_values.shape[1]
        return [GLOBAL_VARIABLE_NAMES[i] if i < len(GLOBAL_VARIABLE_NAMES) else f"GLOBAL{i + 1}" for i in range(n)]

    def global_series(self, name: str) -> np.ndarray:
        return self.global_values[:, self.global_names.index(name)]

    def group(self, title: str) -> THGroup:
        for g in self.groups:
            if g.title == title:
                return g
        raise KeyError(f"no time-history group titled {title!r} (have {[g.title for g in self.groups]})")


class _Records:
    def __init__(self, data: bytes):
        self._b = memoryview(data)
        self._pos = 0

    @property
    def at_end(self) -> bool:
        return self._pos >= len(self._b)

    def next(self) -> memoryview:
        if self._pos + 4 > len(self._b):
            raise THFormatError(f"truncated time-history file at offset {self._pos}")
        n = int(np.frombuffer(self._b[self._pos : self._pos + 4], dtype=">i4")[0])
        end = self._pos + 4 + n
        if n < 0 or end + 4 > len(self._b):
            raise THFormatError(f"truncated time-history record at offset {self._pos}")
        tail = int(np.frombuffer(self._b[end : end + 4], dtype=">i4")[0])
        if tail != n:
            raise THFormatError(f"record length markers disagree at offset {self._pos} ({n} vs {tail})")
        rec = self._b[self._pos + 4 : end]
        self._pos = end + 4
        return rec


def _ints(rec: memoryview, start: int = 0, count: int | None = None) -> np.ndarray:
    count = (len(rec) - start) // 4 if count is None else count
    return np.frombuffer(rec[start : start + 4 * count], dtype=">i4").astype(np.int64)


def _text(rec: memoryview, start: int, width: int) -> str:
    return bytes(rec[start : start + width]).split(b"\0", 1)[0].decode("latin-1").strip()


def read_th_bytes(data: bytes) -> TimeHistory:
    """Parse a time-history file from raw bytes."""
    recs = _Records(data)
    head = recs.next()
    if len(head) < 4:
        raise THFormatError("not a time-history file: empty first record")
    code = int(_ints(head, 0, 1)[0])
    if not 1000 <= code <= 100000:
        raise THFormatError(f"not a time-history file (format code {code})")
    title_len = 100 if code >= 4021 else 80 if code >= 3041 else 40
    recs.next()  # date / code version text
    if code > 3050:
        recs.next()  # additional records
        recs.next()  # title length
        recs.next()  # unit factors
    hierarchy = _ints(recs.next())
    if len(hierarchy) < 6:
        raise THFormatError("time-history hierarchy record is too short")
    n_part, n_mat, n_geo, n_subs, n_groups, n_glob = (int(v) for v in hierarchy[:6])
    if n_glob > 0:
        recs.next()  # global variable numbers

    part_nvar = []
    for _ in range(n_part):
        rec = recs.next()
        nvar = int(_ints(rec, 4 + title_len, 4)[3])
        part_nvar.append(nvar)
        if nvar > 0:
            recs.next()
    for _ in range(n_mat + n_geo):
        recs.next()
    n_subs_var = 0
    for _ in range(n_subs):
        rec = recs.next()
        _, _, n_sub_subs, n_sub_parts, nvar = (int(v) for v in _ints(rec, 0, 5))
        for count in (n_sub_subs, n_sub_parts, nvar):
            if count > 0:
                recs.next()
        n_subs_var += nvar

    groups: list[THGroup] = []
    for _ in range(n_groups):
        rec = recs.next()
        gid, gtype, _, n_obj, nvar = (int(v) for v in _ints(rec, 0, 5))
        g_title = _text(rec, 20, title_len)
        obj_ids, obj_titles = [], []
        for _ in range(n_obj):
            orec = recs.next()
            obj_ids.append(int(_ints(orec, 0, 1)[0]))
            obj_titles.append(_text(orec, 4, title_len))
        var_ids = [int(v) for v in _ints(recs.next())] if nvar > 0 else []
        groups.append(THGroup(gid, gtype, g_title, obj_ids, obj_titles, var_ids))

    n_part_var = sum(part_nvar)
    times, glob = [], []
    rows: list[list[np.ndarray]] = [[] for _ in groups]
    while not recs.at_end:
        try:
            t_rec = recs.next()
            g_rec = recs.next() if n_glob > 0 else None
            if n_part > 0 and n_part_var > 0:
                recs.next()
            if n_subs_var > 0:
                recs.next()
            group_recs = [recs.next() for _ in groups]
        except THFormatError:
            break  # a run killed mid-write leaves a partial last state
        times.append(float(np.frombuffer(t_rec[:4], dtype=">f4")[0]))
        glob.append(np.frombuffer(g_rec, dtype=">f4") if g_rec is not None else np.zeros(0, dtype=">f4"))
        for i, (g, rec) in enumerate(zip(groups, group_recs)):
            rows[i].append(np.frombuffer(rec, dtype=">f4").reshape(len(g.object_ids), len(g.variable_ids)))

    for g, r in zip(groups, rows):
        shape = (0, len(g.object_ids), len(g.variable_ids))
        g.values = np.array(r, dtype=np.float64) if r else np.zeros(shape)
    glob_arr = np.array(glob, dtype=np.float64) if glob else np.zeros((0, n_glob))
    return TimeHistory(np.array(times, dtype=np.float64), glob_arr, groups)


def read_th_file(path: str | pathlib.Path) -> TimeHistory:
    """Parse a time-history file (``<root>T01``) from disk."""
    return read_th_bytes(pathlib.Path(path).read_bytes())
