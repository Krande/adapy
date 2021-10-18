import pytest

from ada import Assembly, Beam, Part
from ada.fem.elements import ElemType


@pytest.fixture
def beam_model_sh() -> Assembly:
    bm = Beam("Bm", (0, 0, 0), (1, 0, 0), "IPE300")
    return Assembly("MyAssembly") / (Part("MyPart", fem=bm.to_fem_obj(0.1, ElemType.SHELL)) / bm)


@pytest.fixture
def beam_model_line() -> Assembly:
    bm = Beam("Bm", (0, 0, 0), (1, 0, 0), "IPE300")
    return Assembly("MyAssembly") / (Part("MyPart", fem=bm.to_fem_obj(0.1, ElemType.LINE)) / bm)


@pytest.fixture
def beam_model_solid() -> Assembly:
    bm = Beam("Bm", (0, 0, 0), (1, 0, 0), "IPE300")
    return Assembly("MyAssembly") / (Part("MyPart", fem=bm.to_fem_obj(0.1, ElemType.SOLID)) / bm)
