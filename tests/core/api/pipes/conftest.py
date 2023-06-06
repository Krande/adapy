import ifcopenshell
import pytest

from ada import Pipe
from ada.cadit.ifc.utils import create_ifc_placement, create_reference_subrep


@pytest.fixture
def pipe_w_single_90_deg_bend(pipe_sec) -> Pipe:
    pipe1 = Pipe(
        "pipe_single_90_deg_bend",
        [
            (0, 0, 0),
            (5, 0, 0),
            (5, 5, 0),
        ],
        pipe_sec,
    )
    return pipe1


@pytest.fixture
def empty_ifc_object():
    f = ifcopenshell.file(schema="IFC4x1")
    _ = create_reference_subrep(f, create_ifc_placement(f))
    return f
