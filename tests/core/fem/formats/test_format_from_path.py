"""``interpret_fem_format_from_path``: the suffix table ``from_fem``/``from_fem_res`` fall back to
when no format is given. ``.odb`` is Abaqus' result file (what ``default_fem_res_path`` writes),
so ``ada.from_fem_res("run.odb")`` must resolve to the Abaqus reader like ``.inp`` does."""

import pytest

from ada.fem.formats.general import FEATypes
from ada.fem.formats.utils import interpret_fem_format_from_path


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("model.fem", FEATypes.SESAM),
        ("model.sif", FEATypes.SESAM),
        ("model.sin", FEATypes.SESAM),
        ("model.inp", FEATypes.ABAQUS),
        ("results.odb", FEATypes.ABAQUS),
        ("model.frd", FEATypes.CALCULIX),
        ("model.rmed", FEATypes.CODE_ASTER),
        ("model.med", FEATypes.CODE_ASTER),
        ("model.rad", FEATypes.OPENCOURANT),
        ("MODEL.INP", FEATypes.ABAQUS),  # the suffix is matched case-insensitively
        ("model.xyz", None),
    ],
)
def test_suffix_maps_to_format(path, expected):
    assert interpret_fem_format_from_path(path) is expected
