import pytest

import ada
from ada.materials.metals import CarbonSteel, DnvGl16Mat


def beam() -> ada.Beam:
    return ada.Beam(
        "MyBeam",
        (0, 0.5, 0.5),
        (3, 0.5, 0.5),
        "IPE400",
        ada.Material("S420", CarbonSteel("S420", plasticity_model=DnvGl16Mat(15e-3, "S355"))),
    )


@pytest.fixture
def beam_fixture() -> ada.Beam:
    return beam()


@pytest.fixture
def short_name_map() -> dict:
    return dict(calculix="ccx", code_aster="ca", abaqus="aba", sesam="ses")


def solver_available(fem_format: str) -> bool:
    """Whether adapy finds the executable it would start for ``fem_format``, the way the format's
    ``LocalExecute.get_exe`` does (for Sesam that includes the Sestra install locator)."""
    import pathlib

    from ada.fem.exceptions import FEASolverNotInstalled
    from ada.fem.formats.abaqus.execute import AbaqusExecute
    from ada.fem.formats.calculix.execute import CalculixExecute
    from ada.fem.formats.code_aster.execute import CodeAsterExecute
    from ada.fem.formats.general import FEATypes
    from ada.fem.formats.sesam.execute import SesamExecute

    executors = {
        FEATypes.CALCULIX: CalculixExecute,
        FEATypes.CODE_ASTER: CodeAsterExecute,
        FEATypes.ABAQUS: AbaqusExecute,
        FEATypes.SESAM: SesamExecute,
    }
    fmt = FEATypes.from_str(fem_format)
    try:
        executors[fmt](pathlib.Path("probe.inp")).get_exe(fmt)
    except (FEASolverNotInstalled, FileNotFoundError):
        return False
    return True


def skip_without(fem_format: str) -> None:
    """Skip (never pass) a solver test on a machine without the solver."""
    if not solver_available(fem_format):
        pytest.skip(f"{fem_format} is not installed")


@pytest.fixture(scope="session")
def require_solver():
    """``require_solver("calculix")`` skips the test (or fixture) when the solver is not installed."""
    return skip_without
