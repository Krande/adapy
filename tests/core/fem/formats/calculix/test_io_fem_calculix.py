import pytest

from ada import Assembly
from ada.fem import LoadGravity, StepImplicitStatic


@pytest.fixture
def test_calculix_dir(test_dir):
    return test_dir / "calculix"


def test_read_C3D20(example_files):
    a = Assembly()
    a.read_fem(example_files / "fem_files/calculix/contact2e.inp")
    beams = list(a.parts.values())[0]
    vol = beams.fem.nodes.vol_cog()
    assert vol == (0.49999999627471, 1.2499999925494, 3.9999999701977)


def test_write_test_model(test_shell_beam, test_calculix_dir):
    a = test_shell_beam

    my_step = StepImplicitStatic("static", total_time=1, max_incr=1, init_incr=1, nl_geom=True)
    my_step.add_load(LoadGravity("Gravity"))
    a.fem.add_step(my_step)

    a.to_fem("my_calculix", fem_format="calculix", overwrite=True, scratch_dir=test_calculix_dir)
