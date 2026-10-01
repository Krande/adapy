"""The appendix's per-mode modal-mass comparison tables (verification/utils.py)."""

from __future__ import annotations

import math
import pathlib
import sys

import pytest

pytest.importorskip("pandas")

_VERIFICATION_DIR = pathlib.Path(__file__).resolve().parents[2] / "verification"
if str(_VERIFICATION_DIR) not in sys.path:
    sys.path.insert(0, str(_VERIFICATION_DIR))

import utils as ru  # noqa: E402

from ada.fem.results.eigenvalue import EigenDataSummary, EigenMode  # noqa: E402


def _case(solver, modes, geo="shell", elo=1):
    return ru.FeaVerificationResult(
        name=f"cantilever_EIG_{solver}_{geo}_o{elo}",
        fem_format=solver,
        metadata={"geo": geo, "elo": elo, "hexquad": True, "reduced_integration": False},
        eig_data=EigenDataSummary(modes),
    )


def test_participation_factors_compare_across_normalisations():
    # One mode, Y bending, 116 kg effective. Abaqus's factor is relative to a displacement-
    # normalised eigenvector (m_gen = 47.6), Sestra's to a mass-normalised one and of opposite sign.
    aba = _case("abaqus", [EigenMode(1, py=1.5635, efy=116.35)])
    ses = _case("sesam", [EigenMode(1, py=-10.787, efy=10.787**2)])

    df = ru.create_modal_mass_comparison_df([aba, ses], "shell", 1, "pf")
    y = df[df["Direction"] == "Y"].iloc[0]
    assert y["aba_QUAD"] == pytest.approx(math.sqrt(116.35), abs=1e-3)
    assert y["ses_QUAD"] == pytest.approx(-10.787, abs=1e-3)
    # X/Z were not reported: empty cells, not zeros
    assert df[df["Direction"] == "X"]["aba_QUAD"].isna().all()


def test_effective_mass_rows_by_mode_and_direction():
    ccx = _case("calculix", [EigenMode(1, efx=0.0, efy=116.0, efz=0.0), EigenMode(2, efx=0.0, efy=0.0, efz=118.0)])
    no_mass = _case("abaqus", [EigenMode(1, f_hz=12.0)])  # a snapshot cached without modal mass

    df = ru.create_modal_mass_comparison_df([ccx, no_mass], "shell", 1, "meff")
    assert list(df.columns) == ["Mode", "Direction", "ccx_QUAD"]
    assert [tuple(r) for r in df[["Mode", "Direction"]].itertuples(index=False)] == [
        (1, "X"),
        (1, "Y"),
        (1, "Z"),
        (2, "X"),
        (2, "Y"),
        (2, "Z"),
    ]
    assert df.loc[(df["Mode"] == 2) & (df["Direction"] == "Z"), "ccx_QUAD"].item() == 118.0


def test_a_configuration_without_data_still_gets_a_table():
    df = ru.create_modal_mass_comparison_df([_case("calculix", [EigenMode(1, efy=1.0)])], "line", 2, "meff")
    assert len(df) == 1 and "Note" in df.columns
