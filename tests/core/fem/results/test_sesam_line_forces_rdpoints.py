"""Beam forces must not depend on how much of RDPOINTS a result deck carries.

RDPOINTS gives an element's result-point count and element type. For line
elements both are also known without it — the type from the mesh, the point
count from the RVFORCES record length — and the line-force decode used that
fallback, but only when RDPOINTS was empty. A deck whose RDPOINTS lists just a
few elements (a force-only run can write one like that) sent every other beam
down the "type unknown" branch, and its RVFORCES records were dropped: the
FORCES field held only the beams that happened to be in RDPOINTS.

The fixture is the static line cantilever, whose RDPOINTS covers all 30 beams.
Each case rewrites it with RDPOINTS thinned out and checks that the FORCES field
is the same as the one read from the untouched deck.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pytest

from ada.fem.formats.sesam.results.read_sif import read_sif_file

_LINE = "cantilever/sesam/static/line/STATIC_LINE_CANTILEVER_SESAMR1.SIF"
_N_BEAMS = 30


def _rewrite_rdpoints(src: pathlib.Path, dst: pathlib.Path, keep: set[int] | None) -> int:
    """Copy ``src`` to ``dst`` keeping only the RDPOINTS records of ``keep``.

    The type-block super-header (first word negative) is kept, unless ``keep``
    is None, which drops the RDPOINTS card altogether. Returns the number of
    RDPOINTS records written.
    """
    out: list[str] = []
    written = 0
    in_rdpoints = False
    keep_record = True
    for line in src.read_text().splitlines(keepends=True):
        if line.startswith("RDPOINTS"):
            in_rdpoints = True
            words = [float(w) for w in line.split()[1:]]
            is_header = words[0] < 0
            # Record layout: NFIELD, ISPALT, IIELNO, ...
            keep_record = keep is not None and (is_header or int(words[2]) in keep)
            written += keep_record
        elif not line[:1].isspace():
            in_rdpoints = False
        if in_rdpoints and not keep_record:
            continue
        out.append(line)
    dst.write_text("".join(out))
    return written


def _forces_by_step(result) -> dict[int, np.ndarray]:
    """Every FORCES row per step, sorted by (element, result point)."""
    rows: dict[int, list[np.ndarray]] = {}
    for field in result.results:
        if field.name == "FORCES":
            rows.setdefault(int(field.step), []).append(np.asarray(field.values, dtype=float))
    out = {}
    for step, blocks in rows.items():
        values = np.vstack(blocks)
        out[step] = values[np.lexsort((values[:, 1], values[:, 0]))]
    return out


@pytest.mark.parametrize(
    ("keep", "n_records"),
    [({1, 2}, 3), (set(), 1), (None, 0)],
    ids=["partial-table", "header-only", "no-rdpoints-card"],
)
def test_line_forces_read_for_beams_missing_from_rdpoints(fem_files, tmp_path, keep, n_records):
    src = fem_files / _LINE
    full = _forces_by_step(read_sif_file(src))
    assert full, "fixture carries no FORCES field"
    for values in full.values():
        assert np.unique(values[:, 0]).size == _N_BEAMS

    thinned_path = tmp_path / src.name
    assert _rewrite_rdpoints(src, thinned_path, keep) == n_records
    thinned = _forces_by_step(read_sif_file(thinned_path))

    assert thinned.keys() == full.keys()
    for step, values in full.items():
        # Every beam, not only those still listed in RDPOINTS, with the values
        # the complete deck gives.
        assert np.unique(thinned[step][:, 0]).size == _N_BEAMS
        np.testing.assert_array_equal(thinned[step], values)
