"""Which runs count as static, and why the answer cannot be a heuristic.

``_infer_analysis_kind`` reads a field's step values as a physical quantity —
eigen frequencies or times — and calls a run modal when they are positive and
ascending. A Sesam deck's step values are result-case NUMBERS, which are
positive and ascending by definition, so a ten-load-case deck came out as ten
mode shapes on its nodal fields while its element fields said static. The two
halves of one manifest disagreed.
"""

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
