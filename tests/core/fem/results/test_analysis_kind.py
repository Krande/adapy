"""Which runs count as static, and why the answer cannot be a heuristic.

``_infer_analysis_kind`` reads a field's step values as a physical quantity —
eigen frequencies or times — and calls a run modal when they are positive and
ascending. A Sesam deck's step values are result-case NUMBERS, which are
positive and ascending by definition, so a ten-load-case deck came out as ten
mode shapes on its nodal fields while its element fields said static. The two
halves of one manifest disagreed.
"""

import json
import pathlib
import shutil

import pytest

from ada.fem.results.artefacts import analysis_kind_from_result_cases


def test_a_combination_is_conclusive():
    # A recipe that superposes basic cases at factors. A modal analysis has no
    # such thing, so this settles it without guessing.
    cases = [
        {"n": 1, "name": "dead"},
        {"n": 2, "name": "live"},
        {"n": 3, "name": "uls", "combination": True, "makeup": "1.2·dead + 1.5·live"},
    ]
    assert analysis_kind_from_result_cases(cases) == "static"


def test_named_cases_alone_prove_nothing():
    # A SESTRA eigen run labels its modes too. Treating names as evidence would
    # force "static" on a mode shape and break the signed deformation sweep it
    # needs, so the heuristic must keep deciding here.
    cases = [{"n": 1, "name": "mode 1"}, {"n": 2, "name": "mode 2"}]
    assert analysis_kind_from_result_cases(cases) is None


def test_no_cases_leaves_the_heuristic_alone():
    assert analysis_kind_from_result_cases(None) is None
    assert analysis_kind_from_result_cases([]) is None


def test_a_falsy_combination_flag_is_not_a_combination():
    assert analysis_kind_from_result_cases([{"n": 1, "combination": False}]) is None


def test_tolerates_entries_that_are_not_dicts():
    # The reader owns this list; a malformed entry must not take the bake down.
    assert analysis_kind_from_result_cases([None, 7, "case", {"combination": True}]) == "static"


def _one_step_result(eigen_freq=None, eigen_mode_data=None):
    import numpy as np

    from ada.fem.formats.general import FEATypes
    from ada.fem.results.common import FEAResult, FemNodes, Mesh
    from ada.fem.results.field_data import NodalFieldData, NodalFieldType

    nodes = FemNodes(coords=np.array([[0.0, 0, 0], [1.0, 0, 0]]), identifiers=np.array([1, 2]))
    disp = NodalFieldData(
        "U",
        1,  # an Abaqus static step's end time / a Sesam load case number: one positive step value
        ["U1", "U2", "U3"],
        np.array([[1, 0, 0, 0], [2, 0, 0, -1e-3]]),
        eigen_freq=eigen_freq,
        field_type=NodalFieldType.DISP,
    )
    return FEAResult("r", FEATypes.ABAQUS, results=[disp], mesh=Mesh([], nodes), eigen_mode_data=eigen_mode_data)


def test_a_single_step_static_result_is_static():
    # The heuristic read one positive step as a mode, and the static deflection was baked as an
    # eigenmode: normalized to a tenth of the model and swept through +/-. The result says it has
    # no modes, which settles it.
    from ada.fem.results.artefacts.stream_adapter import FEAResultStreamAdapter

    (spec,) = FEAResultStreamAdapter(_one_step_result()).field_specs()
    assert spec.analysis_kind == "static"


def test_a_field_with_an_eigen_frequency_is_a_mode():
    from ada.fem.results.artefacts.stream_adapter import FEAResultStreamAdapter

    (spec,) = FEAResultStreamAdapter(_one_step_result(eigen_freq=12.8)).field_specs()
    assert spec.analysis_kind == "eigen"


def test_eigen_data_without_tagged_fields_leaves_the_heuristic_alone():
    from ada.fem.results.artefacts.stream_adapter import FEAResultStreamAdapter
    from ada.fem.results.eigenvalue import EigenDataSummary, EigenMode

    result = _one_step_result(eigen_mode_data=EigenDataSummary([EigenMode(1, f_hz=12.8)]))
    (spec,) = FEAResultStreamAdapter(result).field_specs()
    assert spec.analysis_kind is None


# ---- what the file says ----------------------------------------------------------------------------------------
#
# A mode's frequency and an instant are both positive and ascending, so the step values decide nothing: a time series
# whose first stored instant was not 0 (a window of a run, a solver that does not store t = 0) animated as mode shapes.
# Each reader states the kind from the file instead, and the fallback calls several steps a time series.

FEM_FILES = pathlib.Path(__file__).resolve().parents[4] / "files" / "fem_files"
CA_STATIC = FEM_FILES / "cantilever" / "code_aster" / "static_shell_cantilever_code_aster.rmed"
CA_EIGEN = FEM_FILES / "cantilever" / "code_aster" / "eigen_shell_cantilever_code_aster.rmed"


def _time_series_med(tmp_path, times=(60.0, 61.0, 62.5), field="DISP"):
    """A MED file of one displacement field stored at ``times``, named as another tool would (no Code_Aster concept)
    and with no command file beside it: the static cantilever's displacement, copied to each instant."""
    import h5py

    out = tmp_path / "series.rmed"
    shutil.copy(CA_STATIC, out)
    with h5py.File(out, "r+") as f:
        cha = f["CHA"]
        src = cha["result__DEPL"]
        (key,) = list(src.keys())
        cha.create_group(field)
        for name, value in src.attrs.items():
            cha[field].attrs[name] = value
        for i, t in enumerate(times, start=1):
            step = f"{i:020d}{i:020d}"
            f.copy(src[key], cha[field], name=step)
            cha[field][step].attrs["PDT"] = t
            cha[field][step].attrs["NDT"] = i
            cha[field][step].attrs["NOR"] = i
        for name in list(cha.keys()):
            if name != field:
                del cha[name]
    return out


def _require(path):
    if not path.exists():
        pytest.skip(f"fixture not present: {path.name}")


def test_an_rmed_time_series_starting_at_60_s_is_transient(tmp_path):
    from ada.fem.formats.code_aster.read.med_stream_reader import RmedStreamReader
    from ada.fem.formats.code_aster.results.read_rmed_results import read_rmed_file

    _require(CA_STATIC)
    med = _time_series_med(tmp_path)
    with RmedStreamReader(med) as reader:
        (spec,) = reader.field_specs()
    assert spec.step_values == [60.0, 61.0, 62.5]
    assert spec.analysis_kind == "transient"
    result = read_rmed_file(med)
    assert result.analysis_kind == "transient"
    assert all(r.eigen_freq is None for r in result.results)


def test_a_code_aster_modal_rmed_is_eigen():
    from ada.fem.formats.code_aster.read.med_stream_reader import RmedStreamReader
    from ada.fem.formats.code_aster.results.read_rmed_results import read_rmed_file

    _require(CA_EIGEN)
    with RmedStreamReader(CA_EIGEN) as reader:
        assert {s.analysis_kind for s in reader.field_specs()} == {"eigen"}
    result = read_rmed_file(CA_EIGEN)
    assert result.analysis_kind == "eigen"
    assert all(r.eigen_freq is not None for r in result.results)


def test_a_code_aster_static_single_step_is_static():
    from ada.fem.formats.code_aster.read.med_stream_reader import RmedStreamReader

    _require(CA_STATIC)
    with RmedStreamReader(CA_STATIC) as reader:
        assert {s.analysis_kind for s in reader.field_specs()} == {"static"}


def test_the_command_file_names_each_concept():
    from ada.fem.formats.code_aster.results.analysis_kind import (
        concept_commands,
        field_kind,
    )

    comm = """
modes = CALC_MODES(MATR_RIGI=stiff, MATR_MASS=mass)
modes = NORM_MODE(reuse=modes, MODE=modes, NORME='MASS_GENE')
dyn = DYNA_VIBRA(TYPE_CALCUL='TRAN', BASE_CALCUL='PHYS')
dyn = CALC_CHAMP(reuse=dyn, RESULTAT=dyn, FORCE=('REAC_NODA',))
result = MECA_STATIQUE(MODELE=model)
"""
    commands = concept_commands(comm)
    assert commands == {"modes": "CALC_MODES", "dyn": "DYNA_VIBRA", "result": "MECA_STATIQUE"}
    # The command decides, whatever the step values look like.
    assert field_kind("modes___DEPL", [0.0, 1.0], commands) == "eigen"
    assert field_kind("dyn_____DEPL", [12.0, 13.0, 14.0], commands) == "transient"
    assert field_kind("result__DEPL", [1.0, 2.0], commands) == "static"
    # Without a command file: a concept named for modes is modes, several instants a time series, one a static result,
    # and MACRO_ELAS_MULT's load cases (PDT 999.999 on every case) static.
    assert field_kind("modes___DEPL", [4.6, 6.1]) == "eigen"
    assert field_kind("DISP", [60.0, 61.0]) == "transient"
    assert field_kind("DISP", [60.0]) == "static"
    assert field_kind("result__DEPL", [999.999, 999.999]) == "static"


def test_a_companion_command_file_is_read(tmp_path):
    _require(CA_STATIC)
    from ada.fem.formats.code_aster.read.med_stream_reader import RmedStreamReader

    med = _time_series_med(tmp_path, field="vib_____DEPL")
    with RmedStreamReader(med) as reader:
        assert reader.field_specs()[0].analysis_kind == "transient"
    (tmp_path / "series.comm").write_text("vib = CALC_MODES(MATR_RIGI=k, MATR_MASS=m)\n")
    with RmedStreamReader(med) as reader:
        assert reader.field_specs()[0].analysis_kind == "eigen"


def test_the_fallback_is_static_or_transient_never_eigen():
    from ada.fem.results.artefacts import FieldSpec, _infer_analysis_kind

    def spec(values):
        return FieldSpec("U", ["U1", "U2", "U3"], len(values), 2, "nodal", list(values))

    assert _infer_analysis_kind(spec([])) == "static"
    assert _infer_analysis_kind(spec([1.0])) == "static"
    assert _infer_analysis_kind(spec([60.0, 61.0, 62.0])) == "transient"
    assert _infer_analysis_kind(spec([0.0, 0.5, 1.0])) == "transient"


def test_the_manifest_says_transient_for_a_time_series(tmp_path):
    """The bake carries the reader's kind into the manifest the viewer reads: a timeline at true scale, not a mode
    picker with a signed sweep."""
    from ada.fem.formats.code_aster.read.med_stream_reader import RmedStreamReader
    from ada.fem.results.artefacts import bake_artefacts

    _require(CA_STATIC)
    _require(CA_EIGEN)
    with RmedStreamReader(_time_series_med(tmp_path)) as reader:
        result = bake_artefacts(reader, tmp_path / "series", src="series")
    (field,) = json.loads(result.manifest_path.read_text())["fields"]
    assert field["analysis_kind"] == "transient" and field["n_steps"] == 3

    with RmedStreamReader(CA_EIGEN) as reader:
        result = bake_artefacts(reader, tmp_path / "modes", src="modes")
    assert {f["analysis_kind"] for f in json.loads(result.manifest_path.read_text())["fields"]} == {"eigen"}


def test_calculix_states_the_analysis_type_of_its_blocks():
    from ada.fem.formats.calculix.results.read_frd_file import (
        frd_analysis_kind,
        frd_analysis_type,
    )

    assert frd_analysis_type("  100CL  101 12.67591621         930                     2    1MODAL      1") == 2
    assert frd_analysis_type("  100CL  101 50.00000000         930                     0    1           1") == 0
    assert frd_analysis_kind({2}) == "eigen"
    assert frd_analysis_kind({1}) == "transient"
    assert frd_analysis_kind({0, 3}) == "static"
    assert frd_analysis_kind({0, 2}) is None

    import ada

    for name, kind in (("eigen_shell", "eigen"), ("static_shell", "static")):
        frd = FEM_FILES / "cantilever" / "calculix" / f"{name}_cantilever_calculix.frd"
        _require(frd)
        assert ada.from_fem_res(frd).analysis_kind == kind


@pytest.mark.parametrize(
    "domain, procedure, kind",
    [
        ("MODAL", "*FREQUENCY", "eigen"),
        ("TIME", "*STATIC", "static"),
        ("TIME", "*DYNAMIC", "transient"),
        ("TIME", "*DYNAMIC, EXPLICIT", "transient"),
        ("TIME", "*VISCO", "transient"),
        ("", "", None),
    ],
)
def test_an_abaqus_step_states_its_kind(domain, procedure, kind):
    from ada.fem.formats.abaqus.results.sqlite_stream import abaqus_step_kind

    assert abaqus_step_kind(domain, procedure) == kind
