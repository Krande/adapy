"""STEP import with scale / transform / rotate, on each installed kernel.

These used to force a pythonocc-only reader, which an adacpp-only install does not have,
and which (a) returned millimetres whatever the file's unit, (b) kept only the LAST of the
three transforms -- each gp_Trsf setter replaces the one before -- and (c) dropped names and
colours. They now go through the document backend's OCAF reader as one matrix.

Every measurement is made through the CadBackend of the kernel that read the file.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.api.transforms import Rotation, import_transform_matrix
from ada.cad import check_similarity_matrix

# Two named, coloured 10 x 10 m plates: "blue_plate" at z = 0 and "red_plate" at z = 4.504.
PLATE = "step_files/flat_plate_abaqus_10x10_m_wColors.stp"
Z = 4.504


def _bbox(backend, shapes) -> np.ndarray:
    bb = np.array([backend.bbox(s.solid_occ()) for s in shapes])
    return np.concatenate([bb[:, :3].min(0), bb[:, 3:].max(0)])


@pytest.fixture
def read_with(backend, request, monkeypatch, example_files):
    """Read on ``backend``'s kernel (its doc backend, by name); return (shapes, overall bbox)."""
    monkeypatch.setenv("ADAPY_DOC_BACKEND", request.node.callspec.params["backend"])
    monkeypatch.setattr("ada.cad.doc._ACTIVE_DOC_BACKEND", None)

    def _read(path=None, **kwargs):
        _ = ada.Assembly() / (p := ada.Part("P"))
        p.read_step_file(path if path is not None else example_files / PLATE, **kwargs)
        shapes = list(p.shapes)
        assert shapes
        return shapes, _bbox(backend, shapes)

    return _read


def test_no_transform_reads_metres(read_with):
    # reader="occ" is the document backend's OCAF reader -- the one a transform goes through --
    # so this is the baseline the transformed reads below are compared with.
    _, bb = read_with(reader="occ")
    assert bb == pytest.approx([0, 0, 0, 10, 10, Z], abs=1e-6)


def test_scale(read_with):
    _, bb = read_with(scale=2.0)
    assert bb == pytest.approx([0, 0, 0, 20, 20, 2 * Z], abs=1e-6)


def test_translate(read_with):
    _, bb = read_with(transform=(1.0, 2.0, 3.0))
    assert bb == pytest.approx([1, 2, 3, 11, 12, 3 + Z], abs=1e-6)


def test_rotate(read_with):
    _, bb = read_with(rotate=Rotation((0, 0, 0), (0, 0, 1), 90.0))
    assert bb == pytest.approx([-10, 0, 0, 0, 10, Z], abs=1e-6)


def test_all_three_apply_in_order_scale_rotate_translate(read_with):
    """The old path kept only the last one set. Scale 2 -> 0..20 (z up to 2Z); 90 deg about
    +Z -> x in [-20, 0], y in [0, 20]; then +(1, 2, 3)."""
    _, bb = read_with(scale=2.0, rotate=Rotation((0, 0, 0), (0, 0, 1), 90.0), transform=(1.0, 2.0, 3.0))
    assert bb == pytest.approx([-19, 2, 3, 1, 22, 3 + 2 * Z], abs=1e-6)


def test_names_and_colours_survive_the_transform(read_with):
    plain, _ = read_with(reader="occ")
    moved, _ = read_with(scale=2.0, transform=(1.0, 2.0, 3.0))
    assert [s.color for s in moved] == [s.color for s in plain]
    assert all(s.color is not None for s in moved)


def test_a_directory_of_step_files(read_with, example_files, tmp_path):
    """The docstring always promised a directory; only the old OCC-only path read one."""
    import shutil

    shutil.copy(example_files / PLATE, tmp_path / "a.stp")
    shutil.copy(example_files / PLATE, tmp_path / "b.stp")
    one, _ = read_with(reader="occ")
    both, bb = read_with(tmp_path, transform=(0.0, 0.0, 5.0))
    assert len(both) == 2 * len(one)
    assert bb == pytest.approx([0, 0, 5, 10, 10, 5 + Z], abs=1e-6)


def test_import_transform_matrix_is_none_when_nothing_is_asked():
    assert import_transform_matrix() is None


@pytest.mark.parametrize(
    "matrix",
    [
        np.diag([2.0, 1.0, 1.0, 1.0]),  # a stretch
        np.array([[1, 0.5, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], float),  # a shear
    ],
)
def test_a_non_uniform_matrix_is_refused_by_every_kernel(backend, matrix):
    """gp_Trsf.SetValues would make it uniform without a word (x2 in x -> x1.26 everywhere)."""
    with pytest.raises(ValueError, match="rigid or uniform-scale"):
        check_similarity_matrix(matrix)
    with pytest.raises(ValueError, match="rigid or uniform-scale"):
        backend.transform(backend.make_box(1, 1, 1), matrix)


@pytest.mark.parametrize("matrix", [None, "moved"])
def test_read_step_shapes_agrees_across_kernels(both_backends, example_files, matrix):
    """Both kernels' OCAF reads return the same shapes, names, colours and placement. Until
    this change the pythonocc one returned NOTHING for any STEP assembly: it recursed into the
    component labels instead of following them, and its colour and name lookups were spelled
    for an older pythonocc."""
    m = import_transform_matrix(2.0, (1.0, 2.0, 3.0), Rotation((0, 0, 0), (0, 0, 1), 90.0)) if matrix else None
    data = (example_files / PLATE).read_bytes()

    def summary(be):
        return [
            (d.name, d.has_color, tuple(round(c, 6) for c in d.color), tuple(round(v, 6) for v in be.bbox(d.shape)))
            for d in be.read_step_shapes(data, "M", matrix=m)
        ]

    occ, adacpp = both_backends
    got = summary(occ)
    assert [g[0] for g in got] == ["red_plate", "blue_plate"]
    assert got == summary(adacpp)
