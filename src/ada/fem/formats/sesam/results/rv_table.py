"""A Sesam RV* result table (rows of differing widths) held as arrays.

Shared by the SIN reader (:mod:`read_sin`), which gathers tables straight into this
form, and the result builder (:mod:`read_sif`), which groups and reshapes them
without a Python step per row.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np

from ada.fem.results import sin_native


@dataclass
class _RaggedRows:
    """A per-record RV table (rows of differing widths) as arrays.

    ``heads`` holds each data row's words before its values (``value_start`` of
    them), ``lengths`` each row's count of value words, ``values`` every row's
    value words back to back, in float32 -- the precision NORSAM stores and the
    superposition works in. ``header`` is row 0, the synthesised super-header.
    The rows a reader produces are lists of Python floats: a few hundred bytes
    a row, and a Python loop over every row for every term superposed. This
    form costs about a tenth of that and lets a term be one array operation.
    """

    header: list
    heads: np.ndarray
    lengths: np.ndarray
    values: np.ndarray

    def __len__(self) -> int:
        """Rows including the super-header, like the row list it stands for."""
        return 1 + int(self.heads.shape[0])

    @property
    def offsets(self) -> np.ndarray:
        """Each data row's first value word in :attr:`values`."""
        return np.cumsum(self.lengths) - self.lengths

    @classmethod
    def from_rows(cls, rows, value_start: int, dtype=np.float32) -> "_RaggedRows":
        """From an ndarray table or a row list. ``dtype`` is the values' precision: float32 (the
        superposition's) loses nothing of a SIN's words, but a SIF text deck's may carry more."""
        if isinstance(rows, np.ndarray):
            data = rows[1:]
            return cls(
                rows[0].tolist() if rows.shape[0] else [],
                np.array(data[:, :value_start], dtype=np.float64),
                np.full(data.shape[0], data.shape[1] - value_start, dtype=np.int64),
                np.asarray(data[:, value_start:], dtype=dtype).ravel(),
            )
        rows = list(rows)
        data = rows[1:]
        n = len(data)
        lengths = np.fromiter((len(r) - value_start for r in data), dtype=np.int64, count=n)
        if np.any(lengths < 0):
            raise ValueError(f"an RV row shorter than its {value_start} header words")
        heads = np.array([r[:value_start] for r in data], dtype=np.float64).reshape(n, value_start)
        values = np.fromiter(
            itertools.chain.from_iterable(r[value_start:] for r in data), dtype=np.float64, count=int(lengths.sum())
        ).astype(dtype)
        return cls(list(rows[0]) if rows else [], heads, lengths, values)

    @classmethod
    def from_any(cls, rows, value_start: int, dtype=np.float32) -> "_RaggedRows":
        """``rows`` as a table: an RV table as a reader holds it (this, one ndarray or a row list).
        A table already held as arrays is returned as it is, whatever its precision."""
        if isinstance(rows, _RaggedRows):
            return rows
        return cls.from_rows(rows, value_start, dtype=dtype)

    @classmethod
    def from_records(cls, header: list, n_data: np.ndarray, words: np.ndarray, value_start: int):
        """From :meth:`SinFile.gather_ragged_records`' arrays: the rows
        :func:`_records_for` would build (``[NFIELD, *data]``), without building
        them. ``None`` when a record is too short to hold the header words."""
        n_data = np.asarray(n_data, dtype=np.int64)
        native = sin_native.kernels()
        if native is not None and isinstance(words, np.ndarray) and words.dtype == np.float32 and value_start >= 1:
            split = native.split_records(np.ascontiguousarray(n_data), np.ascontiguousarray(words), int(value_start))
            return None if split is None else cls(list(header), *split)
        lengths = n_data + 1 - value_start
        if np.any(lengths < 0):
            return None
        n = len(n_data)
        n_head = value_start - 1  # header words after NFIELD
        offsets = np.cumsum(n_data) - n_data
        within = np.arange(int(n_data.sum()), dtype=np.int64) - np.repeat(offsets, n_data)
        heads = np.empty((n, value_start), dtype=np.float64)
        heads[:, 0] = n_data + 1
        heads[:, 1:] = words[within < n_head].reshape(n, n_head)
        return cls(list(header), heads, lengths, np.asarray(words[within >= n_head], dtype=np.float32))

    @classmethod
    def concat(cls, first: "_RaggedRows", others) -> "_RaggedRows":
        """``first`` followed by the data rows of each of ``others`` (row 0 kept from ``first``)."""
        parts = [first, *others]
        return cls(
            list(first.header),
            np.concatenate([p.heads for p in parts]),
            np.concatenate([p.lengths for p in parts]),
            np.concatenate([p.values for p in parts]),
        )

    def take(self, rows: np.ndarray, width: int) -> np.ndarray:
        """The value words of ``rows`` (all of ``width`` words) as one ``(len(rows), width)`` array."""
        rows = np.asarray(rows, dtype=np.int64)
        idx = self.offsets[rows][:, None] + np.arange(width, dtype=np.int64)[None, :]
        return self.values[idx]

    def to_rows(self) -> list:
        offsets = np.concatenate(([0], np.cumsum(self.lengths))).tolist()
        values = self.values.tolist()
        return [list(self.header)] + [
            head + values[offsets[k] : offsets[k + 1]] for k, head in enumerate(self.heads.tolist())
        ]
