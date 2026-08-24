"""merge_cells must behave the same on both CAD backends for degenerate operand counts.

OCCT's BOPAlgo_CellsBuilder is a general-fuse of >= 2 operands: handed one solid (or
none) it reports an error instead of doing nothing. OccBackend guards that; the adacpp
backend is a thin passthrough to C++ and used to hand the single solid straight to
OCCT, so a one-cell model built fine on OCC and failed on native with the generic
"BOPAlgo_CellsBuilder reported errors".
"""

from __future__ import annotations

import pytest

from ada.cad import AdacppBackend


class _StubCad:
    """Stands in for ``adacpp.cad`` so the guard is testable without the native build."""

    def __init__(self, raises: bool = False):
        self.calls: list[tuple[int, float]] = []
        self._raises = raises

    def merge_cells(self, solids, tolerance):
        self.calls.append((len(solids), tolerance))
        if self._raises:
            raise RuntimeError("merge_cells: BOPAlgo_CellsBuilder reported errors")
        return list(solids)


def _backend(stub: _StubCad) -> AdacppBackend:
    # Bypass __init__ so no real adacpp import is needed.
    be = AdacppBackend.__new__(AdacppBackend)
    be._cad = stub
    return be


@pytest.mark.parametrize("solids", [[], ["solid-a"]])
def test_merge_cells_short_circuits_below_two_operands(solids):
    stub = _StubCad(raises=True)

    assert _backend(stub).merge_cells(solids, tolerance=1e-4) == solids
    assert stub.calls == []  # OCCT never saw the degenerate call


def test_merge_cells_still_delegates_two_or_more_operands():
    stub = _StubCad()

    assert _backend(stub).merge_cells(["a", "b"], tolerance=1e-4) == ["a", "b"]
    assert stub.calls == [(2, 1e-4)]


def test_merge_cells_error_carries_operand_count_and_tolerance():
    stub = _StubCad(raises=True)

    with pytest.raises(RuntimeError) as excinfo:
        _backend(stub).merge_cells(["a", "b", "c"], tolerance=1e-4)

    message = str(excinfo.value)
    assert "BOPAlgo_CellsBuilder reported errors" in message
    assert "operands=3" in message
    assert "0.0001" in message
