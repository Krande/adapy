"""Model-property fields: thickness / material / section as paintable fields.

They ride the ordinary element-field pipeline (blobs, ranges, flat colouring)
but are input data, not analysis output — pinned here: the ``property``
category that keeps them out of results pickers, the single-step contract,
and the id -> label mapping a categorical legend needs.
"""

import numpy as np

from ada.fem.formats.sesam.results.property_fields import (
    MATERIAL_FIELD,
    SECTION_FIELD,
    THICKNESS_FIELD,
)
from ada.fem.formats.sesam.results.read_sif import read_sif_file
from ada.fem.results.artefacts import FEAResultStreamAdapter


def _by_name(result):
    out = {}
    for r in result.results:
        out.setdefault(r.name, []).append(r)
    return out


def test_shell_deck_carries_thickness_and_material(fem_files):
    result = read_sif_file(fem_files / "sesam/2EL_SHELL_R1.SIF")
    fields = _by_name(result)

    th = fields[THICKNESS_FIELD][0]
    assert th.presentation.unit == "m"
    values = th.values[:, 2]
    assert np.isfinite(values).all() and (values > 0).all()

    mat = fields[MATERIAL_FIELD][0]
    labels = dict(mat.presentation.value_labels)
    # Every stored id has a printable name.
    for stored in np.unique(mat.values[:, 2]):
        assert labels[float(stored)]

    # A pure shell deck has no beam sections to name.
    assert SECTION_FIELD not in fields


def test_line_deck_carries_named_sections(fem_files):
    result = read_sif_file(fem_files / "cantilever/sesam/static/line/STATIC_LINE_CANTILEVER_SESAMR1.SIF")
    fields = _by_name(result)

    sec = fields[SECTION_FIELD][0]
    labels = dict(sec.presentation.value_labels)
    assert labels, "expected at least one named section"
    for stored in np.unique(sec.values[:, 2]):
        assert labels[float(stored)]


def test_property_specs_are_single_step_and_categorised(fem_files):
    result = read_sif_file(fem_files / "sesam/2EL_SHELL_R1.SIF")
    adapter = FEAResultStreamAdapter(result)
    props = [s for s in adapter.element_field_specs() if s.category == "property"]
    names = {s.name for s in props}
    assert {THICKNESS_FIELD, MATERIAL_FIELD} <= names
    for spec in props:
        assert spec.n_steps == 1
        assert spec.support == "element_average"
        assert spec.n_ips == 1


def test_properties_ride_the_loaded_step_and_vanish_with_it(fem_files):
    # A step-filtered read stays single-step; an empty read stays empty.
    eigen = "cantilever/sesam/eigen/shell/EIGEN_SHELL_CANTILEVER_SESAMR1.SIF"
    res3 = read_sif_file(fem_files / eigen, step=3)
    assert res3.get_steps() == [3]
    assert THICKNESS_FIELD in _by_name(res3)

    res_none = read_sif_file(fem_files / eigen, step=999)
    assert res_none.get_steps() == []
    assert THICKNESS_FIELD not in _by_name(res_none)
