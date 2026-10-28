"""FEM names longer than some FEM software accepts: one notice per write, none on read."""

from __future__ import annotations

import contextlib
import logging

import ada
from ada.fem import FemSet
from ada.fem.formats.general import LONG_FEM_NAME, report_long_fem_names


@contextlib.contextmanager
def _ada_records(level=logging.DEBUG):
    """The ``ada`` logger's records (it does not propagate, so ``caplog`` sees none)."""
    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("ada")
    handler = _Collect(level=level)
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(level)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


def _long_name_notices(records):
    return [r for r in records if f"longer than {LONG_FEM_NAME} characters" in r.getMessage()]


def _model(num_long: int, long_name_len: int = 40) -> ada.Assembly:
    part = ada.Part("part_a") / ada.Beam("bm_a", (0, 0, 0), (1, 0, 0), "IPE300")
    part.fem = part.to_fem_obj(0.5)
    nodes = list(part.fem.nodes)
    for i in range(num_long):
        part.fem.add_set(FemSet(f"set_{i:03d}_".ljust(long_name_len, "x"), nodes[:2], "nset"))
    part.fem.add_set(FemSet("short_set", nodes[:1], "nset"))
    return ada.Assembly("asm") / part


def test_creating_fem_objects_with_long_names_logs_nothing():
    """A reader creates FEM objects by the thousand; the name length is no concern of reading."""
    with _ada_records() as records:
        sets = [FemSet(f"set_{i:03d}_".ljust(60, "x"), [], "elset") for i in range(50)]

    assert len(sets) == 50
    assert _long_name_notices(records) == []


def test_a_write_reports_long_names_once_with_a_count_and_a_few_examples(tmp_path):
    model = _model(num_long=12)

    with _ada_records(logging.INFO) as records:
        model.to_fem("long_names", "abaqus", scratch_dir=tmp_path, overwrite=True)

    notices = _long_name_notices(records)
    assert len(notices) == 1
    message = notices[0].getMessage()
    assert message.startswith("12 FEM name(s)")
    assert message.count('"set_') == 3
    assert message.endswith(", ...")
    assert "short_set" not in message


def test_no_notice_without_long_names():
    with _ada_records() as records:
        assert report_long_fem_names(_model(num_long=0)) == []
    assert _long_name_notices(records) == []


def test_the_notice_is_bounded_whatever_the_name_length():
    with _ada_records() as records:
        long_names = report_long_fem_names(_model(num_long=2, long_name_len=5000))

    assert [len(n) for n in long_names] == [5000, 5000]
    (notice,) = _long_name_notices(records)
    assert len(notice.getMessage()) < 400
