import importlib.util
import os
import pathlib

import pytest

import ada
from ada.cad import CadBackendName, backend_available, select_backend
from ada.config import Config

is_printed = False
TESTS_DIR = pathlib.Path(__file__).resolve().absolute().parent
ROOT_DIR = TESTS_DIR.parent


@pytest.fixture(autouse=True)
def clear_config_instances():
    Config._instances = {}


@pytest.fixture
def this_dir() -> pathlib.Path:
    return TESTS_DIR


@pytest.fixture
def root_dir() -> pathlib.Path:
    return ROOT_DIR


@pytest.fixture
def example_files(this_dir) -> pathlib.Path:
    return ROOT_DIR / "files"


@pytest.fixture
def fem_files(example_files) -> pathlib.Path:
    return example_files / "fem_files"


@pytest.fixture
def plate1():
    return ada.Plate("MyPlate", [(0, 0), (1, 0), (1, 1), (0, 1)], 20e-3)


@pytest.fixture
def bm_ipe300():
    return ada.Beam("MyIPE300", (0, 0, 0), (5, 0, 0), "IPE300")


@pytest.fixture
def basic_2d_plate():
    return ada.Plate(
        "MyPl",
        [(0, 0, 0.2), (5, 0), (5, 5), (0, 5)],
        20e-3,
        placement=ada.Placement(origin=(0, 0, 0), xdir=(1, 0, 0), zdir=(0, 0, 1)),
    )


@pytest.fixture
def pipe_sec() -> ada.Section:
    return ada.Section("PSec", "PIPE", r=0.10, wt=5e-3)


@pytest.fixture
def pipe_w_multiple_bends(pipe_sec) -> ada.Pipe:
    z = 3.2
    y0 = -200e-3
    x0 = -y0
    coords = [
        (0, y0, z),
        (5 + x0, y0, z),
        (5 + x0, y0 + 5, z),
        (10, y0 + 5, z + 2),
        (10, y0 + 5, z + 10),
    ]
    pipe1 = ada.Pipe(
        "Pipe1",
        coords,
        pipe_sec,
    )
    return pipe1


@pytest.fixture
def mixed_model(pipe_w_multiple_bends, basic_2d_plate):
    bm1 = ada.Beam("bm1", (0, 0, 0), (1, 0, 0), "HP140x8")
    bm2 = ada.Beam("bm2", (0, 1, 0), (1, 1, 0), "HP140x8")
    bm3 = ada.Beam("bm3", (0, 2, 0), (1, 2, 0), "HP140x8")

    mix1 = [bm1, pipe_w_multiple_bends]
    mix2 = [bm2, basic_2d_plate]

    return ada.Assembly() / [(ada.Part("P1") / mix1), (ada.Part("P2") / mix2), (ada.Part("P3") / bm3)]


# --- CAD backend fixtures ----------------------------------------------------------------------
# Tests reach a kernel only through the CadBackend API, and measure a shape with the backend that
# built it: a shape is only readable by its own kernel. ``select_backend`` tries adacpp before
# pythonocc, so every backend handed out here is pinned by name rather than auto-selected.

BACKEND_NAMES = ("occ", "adacpp")


#: The `occ` leg carries the `pyocc` marker so a run can DESELECT it rather than skip it. A
#: parametrised backend test is one test per kernel: the adacpp leg belongs in every run, the
#: pythonocc leg only where that kernel exists. Marking the param (not the test) is what keeps
#: those two facts separable -- the alternative, skipping at fixture time, reports a hole in
#: every default run for a kernel that env was never meant to carry.
_BACKEND_PARAMS = (
    pytest.param("occ", marks=pytest.mark.pyocc),
    pytest.param("adacpp"),
)


@pytest.fixture(params=_BACKEND_PARAMS)
def backend(request):
    """One installed backend, pinned by name — never the ``select_backend`` default."""
    if not backend_available(CadBackendName(request.param)):
        pytest.skip(f"{request.param} backend not installed")
    return select_backend(prefer=request.param)


@pytest.fixture
def occ_backend():
    """The pythonocc backend, for tests whose subject IS that kernel; a skip where it's absent."""
    if not backend_available(CadBackendName.OCC):
        pytest.skip("occ backend not installed")
    return select_backend(prefer="occ")


@pytest.fixture
def both_backends():
    """``(occ, adacpp)``, or a skip when this environment carries only one kernel."""
    missing = [n for n in BACKEND_NAMES if not backend_available(CadBackendName(n))]
    if missing and os.environ.get("ADAPY_REQUIRE_BOTH_KERNELS"):
        # tests-xkernel exists to run these; a kernel that fails to import there must not
        # turn the whole job into a green run of skips.
        pytest.fail(f"ADAPY_REQUIRE_BOTH_KERNELS is set but a kernel is missing: {', '.join(missing)}")
    if missing:
        pytest.skip(f"cross-backend comparison needs both kernels; missing: {', '.join(missing)}")
    return select_backend(prefer="occ"), select_backend(prefer="adacpp")


def pytest_collection_modifyitems(config, items):
    """Mark every test that needs pythonocc, so a run can select or deselect the whole set.

    DERIVED, NOT DECLARED. A test needs that kernel because of the FIXTURE it asks for -- and a
    marker maintained by hand beside the fixture is one someone will forget on the next test.
    `fixturenames` already carries the answer, so this reads it rather than trusting a second
    source. `backend`'s own `occ` leg is marked at the param instead (see `_BACKEND_PARAMS`),
    because there the kernel is one of two legs rather than the whole test.

    The point is to stop the default suite REPORTING these as skips: an env without pythonocc was
    never meant to run them, and 117 skipped tests read as missing coverage rather than as
    coverage that lives in another job.
    """
    needs_occ = {"occ_backend", "both_backends"}
    for item in items:
        if needs_occ.intersection(getattr(item, "fixturenames", ())):
            item.add_marker(pytest.mark.pyocc)

    # DESELECT, don't skip. An environment without pythonocc was never going to run these, and a
    # skip reports that as a hole in the suite -- one that is never read, never acted on, and
    # never executed anywhere. Removing them from collection says the same thing honestly: this
    # env runs what it can run, and the compat leg (an env carrying both kernels) runs the rest.
    #
    # `ADAPY_REQUIRE_BOTH_KERNELS` turns this off: that is the compat leg, where a missing kernel
    # must FAIL rather than quietly shrink the run to nothing.
    absent = set()
    # `pyocc` is exempt under ADAPY_REQUIRE_BOTH_KERNELS: that is the compat leg, where a missing
    # kernel must FAIL rather than quietly shrink the run to nothing.
    if not os.environ.get("ADAPY_REQUIRE_BOTH_KERNELS") and not backend_available(CadBackendName.OCC):
        absent.add("pyocc")
    if importlib.util.find_spec("medcoupling") is None:
        absent.add("medcoupling")
    # Symmetric with `pyocc`: the pythonocc-only envs have no adacpp, and a test whose subject is
    # that kernel is no more "skipped" there than a pythonocc test is here.
    if importlib.util.find_spec("adacpp") is None:
        absent.add("adacpp")
    if not absent:
        return

    kept, removed = [], []
    for item in items:
        (removed if any(item.get_closest_marker(m) for m in absent) else kept).append(item)
    if removed:
        config.hook.pytest_deselected(items=removed)
        items[:] = kept
