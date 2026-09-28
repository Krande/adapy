"""Mode-shape normalization: one mode from any solver bakes at the same amplitude and sign.

Solvers scale eigenvectors by their own convention (max component = 1, mass-normalized, ...) and
may return either sign, so the same physical mode arrives several times larger or flipped
depending on who computed it.
"""

import json

import numpy as np
import pytest

from ada.fem.results.artefacts.mode_normalization import (
    DEFAULT_TARGET_FRACTION,
    mode_scale_factor,
    reference_length,
    translation_columns,
)

POINTS = np.array(
    [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0], [0.0, 1.0, 0.0], [1.0, 1.0, 0.0], [2.0, 1.0, 0.0]]
)


def _bending_mode():
    """A cantilever-like bending shape in z, growing along x."""
    z = (POINTS[:, 0] / 2.0) ** 2
    return np.column_stack([np.zeros(6), np.zeros(6), z])


def test_translation_columns_are_found_by_name():
    # Sesam's displacement leads with ALL, a reduction -- not an axis.
    assert translation_columns(["ALL", "X", "Y", "Z", "RX", "RY", "RZ"]) == [1, 2, 3]
    assert translation_columns(["D1", "D2", "D3"]) == [0, 1, 2]
    assert translation_columns(["DX", "DY", "DZ", "DRX", "DRY", "DRZ"]) == [0, 1, 2]
    # Names that are not axes: the historical positional reading.
    assert translation_columns(["A", "B", "C", "D"]) == [0, 1, 2]
    assert translation_columns(["A", "B"]) == [0, 1]


@pytest.mark.parametrize("solver_scale", [1.0, 0.1439, -2.5, -0.2])
def test_the_same_mode_normalizes_to_the_same_values_whatever_its_scale_and_sign(solver_scale):
    mode = _bending_mode()
    ref = reference_length(POINTS)
    normalized = mode * solver_scale * mode_scale_factor(mode * solver_scale, POINTS, [0, 1, 2], ref)
    expected = mode * mode_scale_factor(mode, POINTS, [0, 1, 2], ref)
    np.testing.assert_allclose(normalized, expected, rtol=1e-12, atol=1e-12)
    assert np.linalg.norm(normalized, axis=1).max() == pytest.approx(DEFAULT_TARGET_FRACTION * ref)
    assert normalized[:, 2].max() > 0  # the energy-weighted motion points +z


def test_an_antisymmetric_mode_gets_a_stable_sign_too():
    # Torsion-like: z motion of opposite sign on the two long edges, summing to zero.
    twist = np.column_stack([np.zeros(6), np.zeros(6), np.where(POINTS[:, 1] > 0.5, 1.0, -1.0) * POINTS[:, 0]])
    ref = reference_length(POINTS)
    a = twist * mode_scale_factor(twist, POINTS, [0, 1, 2], ref)
    b = -twist * mode_scale_factor(-twist, POINTS, [0, 1, 2], ref)
    np.testing.assert_allclose(a, b)


def test_nothing_to_normalize_leaves_the_mode_alone():
    assert mode_scale_factor(np.zeros((6, 3)), POINTS, [0, 1, 2], reference_length(POINTS)) == 1.0
    assert mode_scale_factor(_bending_mode(), POINTS, [0, 1, 2], 0.0) == 1.0


def _eigen_result(scales):
    """A two-quad plate with one bending mode per entry of ``scales`` (the solver's normalization)."""
    from ada.fem.formats.general import FEATypes
    from ada.fem.results.common import (
        ElementBlock,
        ElementInfo,
        FEAResult,
        FemNodes,
        Mesh,
    )
    from ada.fem.results.field_data import NodalFieldData, NodalFieldType
    from ada.fem.shapes.definitions import ShellShapes

    ids = np.arange(1, 7)
    nodes = FemNodes(coords=POINTS.copy(), identifiers=ids)
    block = ElementBlock(
        elem_info=ElementInfo(type=ShellShapes.QUAD, source_software=FEATypes.CALCULIX, source_type="S4"),
        node_refs=np.array([[1, 2, 5, 4], [2, 3, 6, 5]]),
        identifiers=np.array([1, 2]),
    )
    fields = [
        NodalFieldData(
            "DISP",
            step,
            ["D1", "D2", "D3"],
            np.column_stack([ids, _bending_mode() * scale]),
            eigen_freq=10.0 * step,
            field_type=NodalFieldType.DISP,
        )
        for step, scale in enumerate(scales, start=1)
    ]
    return FEAResult(name="plate", software=FEATypes.CALCULIX, results=fields, mesh=Mesh(elements=[block], nodes=nodes))


def _baked_modes(out_dir, scales, normalize):
    from ada.fem.results.artefacts import bake_artefacts
    from ada.fem.results.artefacts.stream_adapter import FEAResultStreamAdapter
    from ada.visit.rendering.fea_offscreen import _list_displacement_entries, _parse_afbl

    bake = bake_artefacts(
        FEAResultStreamAdapter(_eigen_result(scales)),
        out_dir,
        include_element_fields=False,
        include_beam_solids=False,
        normalize_modes=normalize,
    )
    manifest = json.loads(bake.manifest_path.read_text(encoding="utf-8"))
    modes = []
    for url, step in _list_displacement_entries(manifest):
        _, arr = _parse_afbl((out_dir / url).read_bytes())
        modes.append(arr[step])
    field = next(f for f in manifest["fields"] if f["category"] == "displacement")
    return modes, field


def test_the_bake_normalizes_eigenmodes_and_records_the_factors(tmp_path):
    scales = [0.1439, -1.0]  # one mode, as two solvers might return it
    modes, field = _baked_modes(tmp_path / "norm", scales, normalize=True)
    np.testing.assert_allclose(modes[0], modes[1], rtol=1e-5, atol=1e-7)

    record = field["mode_normalization"]
    assert record["method"] == "max_translation"
    assert record["target_fraction"] == DEFAULT_TARGET_FRACTION
    # The solver's raw values stay recoverable: raw = baked / factor.
    for mode, scale, factor in zip(modes, scales, record["factors"]):
        np.testing.assert_allclose(mode / factor, _bending_mode() * scale, rtol=1e-5, atol=1e-7)


def test_the_bake_leaves_modes_raw_by_default(tmp_path):
    modes, field = _baked_modes(tmp_path / "raw", [0.1439, -1.0], normalize=False)
    assert "mode_normalization" not in field
    np.testing.assert_allclose(modes[1], -_bending_mode(), rtol=1e-6)
