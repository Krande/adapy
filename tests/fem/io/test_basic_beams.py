import logging

import pytest

from ada import Assembly, Beam, Part
from ada.config import Settings
from ada.fem import Bc, FemSet, Step
from ada.fem.elements import ElemType
from ada.fem.exceptions import IncompatibleElements
from ada.fem.io import FEATypes
from ada.fem.utils import get_beam_end_nodes


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


@pytest.mark.parametrize("fem_format", FEATypes.all)
@pytest.mark.parametrize("geom_repr", ElemType.all)
@pytest.mark.parametrize("elem_order", [1, 2])
def test_beam_eig(
    beam_model_sh: Assembly, beam_model_solid: Assembly, beam_model_line: Assembly, fem_format, geom_repr, elem_order
):
    model_map = {
        ElemType.LINE: beam_model_line,
        ElemType.SHELL: beam_model_sh,
        ElemType.SOLID: beam_model_solid,
    }

    a: Assembly = model_map.get(geom_repr)
    bm: Beam = a.get_by_name("Bm")

    name = f"bm_{fem_format}_{geom_repr}_o{elem_order}"
    fix_set = a.get_part("MyPart").fem.add_set(FemSet("bc_nodes", get_beam_end_nodes(bm), FemSet.TYPES.NSET))
    a.fem.add_bc(Bc("Fixed", fix_set, [1, 2, 3, 4, 5, 6]))
    a.fem.add_step(Step("Eigen", Step.TYPES.EIGEN, eigenmodes=11))

    scratch_dir = Settings.scratch_dir / "basic_beam_validate"

    res = None
    try:
        res = a.to_fem(name, fem_format, overwrite=True, scratch_dir=scratch_dir)
    except IncompatibleElements as e:
        if fem_format == "calculix" and geom_repr == "line":
            logging.error(e)
            return None
        elif fem_format == "code_aster" and geom_repr == "line" and elem_order == 2:
            logging.error(e)
            return None
        elif fem_format == "sesam" and geom_repr == "solid":
            # TODO: Add write support for solid elements
            logging.error(e)
            return None
        raise e
    finally:
        print(res)


if __name__ == "__main__":
    retcode = pytest.main([])
    print(retcode)
