"""The Code_Aster MED result carries only the mode frequency; the reader
derives the circular frequency and the eigenvalue from it."""

import math

import h5py
import pytest

from ada.fem.formats.code_aster.results.results import get_eigen_data


def test_eigenvalue_is_derived_from_the_frequency(tmp_path):
    rmed = tmp_path / "modes.rmed"
    with h5py.File(rmed, "w") as f:
        modes = f.create_group("CHA/modes___DEPL")
        for no, freq in ((1, 12.78), (2, 40.5)):
            step = modes.create_group(f"{no:020d}")
            step.attrs["NDT"] = no
            step.attrs["PDT"] = freq

    mode1, mode2 = get_eigen_data(rmed).modes

    assert mode1.f_hz == pytest.approx(12.78)
    assert mode1.f_rad == pytest.approx(2 * math.pi * 12.78)
    # Same mode from Abaqus on the verification cantilever: 6448.4.
    assert mode1.eigenvalue == pytest.approx(6447.9, rel=1e-4)
    assert mode2.eigenvalue == pytest.approx((2 * math.pi * 40.5) ** 2)
