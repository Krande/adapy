"""``read_shell_pressures``: a SIN's BEUSLO records as the mean shell pressure per load case and element.

The records of one element and case add, whatever their SIDE (Sestra V11.3 adds two records on one element: 400 +
600 Pa gave the response of 1000 Pa, ``tests/fem/test_sesam_pressure_load.py``); the last one used to replace the
others. Only LOTYP 1 is a pressure: a LOTYP 2 record (a traction vector, three components per node, which GeniE
writes) is left out rather than averaged into one.
"""

from __future__ import annotations

from ada.fem.formats.sesam.results.structural import Numbering, read_shell_pressures


class _Sin:
    def __init__(self, records):
        self.type_blocks = {"BEUSLO": object()}
        self._records = records

    def iter_records(self, name):
        return iter(self._records) if name == "BEUSLO" else iter(())


def _beuslo(llc, element, values, lotyp=1, side=1, complx=0):
    # LLC LOTYP COMPLX LAYER / ELNO NDOF INTNO SIDE / RLOAD1..n
    return [llc, lotyp, complx, 0, element, len(values), 0, side, *values]


def test_records_of_one_element_and_case_add_whatever_their_side():
    sin = _Sin(
        [
            _beuslo(1, 7, [-400.0] * 4, side=1),
            _beuslo(1, 7, [-600.0] * 4, side=2),
            _beuslo(1, 8, [-400.0] * 4),
            _beuslo(2, 7, [50.0, 50.0, 70.0, 70.0]),
        ]
    )
    assert read_shell_pressures(sin, Numbering()) == {1: {7: -1000.0, 8: -400.0}, 2: {7: 60.0}}


def test_a_vector_load_record_is_no_pressure(monkeypatch):
    from ada.config import logger

    warned = []
    monkeypatch.setattr(logger, "warning", lambda msg, *a, **k: warned.append(msg))
    vector = [100.0, 0.0, -1000.0] * 4  # LOTYP 2: (x, y, z) at each of the four nodes
    sin = _Sin([_beuslo(1, 7, [-1000.0] * 4), _beuslo(1, 7, vector, lotyp=2), _beuslo(2, 9, vector, lotyp=2)])
    assert read_shell_pressures(sin, Numbering()) == {1: {7: -1000.0}}
    assert len(warned) == 1 and "2 BEUSLO record(s) of LOTYP 2" in warned[0]
