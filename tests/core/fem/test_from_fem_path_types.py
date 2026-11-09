import os

import pytest

import ada
from ada.factories import _from_fem


class _PathLike(os.PathLike):
    def __fspath__(self):
        return "model.inp"


def _read(fem_file, monkeypatch):
    seen = []
    monkeypatch.setattr(ada.Assembly, "read_fem", lambda self, f, *a, **k: seen.append(f))
    _from_fem(fem_file, None, None, "m", "default", False, False, False, None)
    return seen


def test_any_path_like_is_accepted(monkeypatch):
    fem_file = _PathLike()
    assert _read(fem_file, monkeypatch) == [fem_file]


def test_anything_else_is_refused(monkeypatch):
    with pytest.raises(ValueError, match="fem_file must be"):
        _read(5, monkeypatch)
