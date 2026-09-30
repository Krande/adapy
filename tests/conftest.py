import functools
import importlib.util
import os
import pathlib
import shutil
import tempfile

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
    pytest.param("adacpp", marks=pytest.mark.adacpp),
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
    # An escape hatch, for asking "would these actually run here?" -- which is the only way to
    # catch a test marked for a capability it does not really need. Without it the deselection is
    # invisible to inspection: `-m adacpp` selects nothing, because the deselection already ran.
    if os.environ.get("ADAPY_NO_CAPABILITY_DESELECT"):
        return
    if not absent:
        return

    kept, removed = [], []
    for item in items:
        (removed if any(item.get_closest_marker(m) for m in absent) else kept).append(item)
    if removed:
        config.hook.pytest_deselected(items=removed)
        items[:] = kept


# --- ifcopenshell geometry: probe once, degrade to skips on a crashing build ----------------------
# A broken ifcopenshell geometry build (conda-forge 0.9.0 on macOS: flat-namespace dylibs that
# abort in dyld on first use) does not raise -- it kills the interpreter, and with it the whole
# run. So before any test runs, the minimal case (an extrusion through `geom.iterator` and
# `geom.create_shape`, per schema) is run in child processes. On a healthy build nothing else
# happens. On a broken one:
#   * tests marked `ifcgeom` are skipped up front, the reason naming the probe result;
#   * `ifcopenshell.geom.iterator` / `create_shape` are wrapped so that any OTHER test reaching
#     them (through adapy's readers, tessellators, ifc2sql, ...) skips at that call instead of
#     aborting -- the marker cannot know every indirect caller, the wrapper does not need to;
#   * `tests/core/cadit/ifc/test_ifcopenshell_geometry_health.py` still FAILS, out of process,
#     so the run is red for the right reason and says exactly what is broken.
# Set ADAPY_IFCGEOM_PROBE=0 to disable (e.g. to watch the real crash).

_IFCGEOM_PROBE = None
_IFCGEOM_ORIGINALS: dict = {}


def _ifcgeom_skip_reason(schema=None) -> str | None:
    failure = _IFCGEOM_PROBE.failure(schema) if _IFCGEOM_PROBE is not None else None
    if failure is None:
        return None
    return (
        f"ifcopenshell geometry crashes in this environment (session probe: {failure}); "
        "see test_ifcopenshell_geometry_health.py for the per-schema/operation report"
    )


def _schema_of(obj) -> str | None:
    """Schema of an ``ifcopenshell.file`` or ``entity_instance`` (None when unknown)."""
    try:
        if hasattr(obj, "schema_identifier"):
            return obj.schema_identifier
        return obj.is_a(True).split(".")[0]
    except Exception:
        return None


def _install_ifcgeom_guards() -> None:
    import ifcopenshell.geom

    orig_iterator, orig_create_shape = ifcopenshell.geom.iterator, ifcopenshell.geom.create_shape
    _IFCGEOM_ORIGINALS.update(iterator=orig_iterator, create_shape=orig_create_shape)

    class GuardedIterator(orig_iterator):  # a subclass, so isinstance() checks keep working
        def __init__(self, settings, file_or_filename, *args, **kwargs):
            reason = _ifcgeom_skip_reason(_schema_of(file_or_filename))
            if reason:
                pytest.skip(f"ifcopenshell.geom.iterator: {reason}")
            super().__init__(settings, file_or_filename, *args, **kwargs)

    @functools.wraps(orig_create_shape)
    def guarded_create_shape(settings, inst, *args, **kwargs):
        reason = _ifcgeom_skip_reason(_schema_of(inst))
        if reason:
            pytest.skip(f"ifcopenshell.geom.create_shape: {reason}")
        return orig_create_shape(settings, inst, *args, **kwargs)

    GuardedIterator.__name__ = GuardedIterator.__qualname__ = "iterator"
    ifcopenshell.geom.iterator = GuardedIterator
    ifcopenshell.geom.create_shape = guarded_create_shape


@pytest.hookimpl(tryfirst=True)
def pytest_collection_finish(session):
    """Probe after collection (so `--collect-only` and empty runs never pay for it)."""
    global _IFCGEOM_PROBE
    config = session.config
    if not session.items or config.option.collectonly or os.environ.get("ADAPY_IFCGEOM_PROBE", "1") == "0":
        return
    from tests.ifcgeom_probe import probe

    workdir = tempfile.mkdtemp(prefix="ifcgeom_probe_")
    try:
        _IFCGEOM_PROBE = probe(workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    tr = config.pluginmanager.get_plugin("terminalreporter")
    if _IFCGEOM_PROBE.healthy:
        if tr is not None:
            tr.write_line(_IFCGEOM_PROBE.report())
        return
    _install_ifcgeom_guards()
    reason = _ifcgeom_skip_reason()
    for item in session.items:
        if item.get_closest_marker("ifcgeom"):
            item.add_marker(pytest.mark.skip(reason=reason))
    if tr is not None:
        tr.write_line(_IFCGEOM_PROBE.report(), red=True)


def pytest_terminal_summary(terminalreporter):
    """Repeat a broken probe at the END too, where a CI log reader actually looks."""
    if _IFCGEOM_PROBE is not None and not _IFCGEOM_PROBE.healthy:
        terminalreporter.section("ifcopenshell geometry probe", red=True)
        terminalreporter.write_line(_IFCGEOM_PROBE.report())


def pytest_unconfigure(config):
    if _IFCGEOM_ORIGINALS:
        import ifcopenshell.geom

        for name, fn in _IFCGEOM_ORIGINALS.items():
            setattr(ifcopenshell.geom, name, fn)
