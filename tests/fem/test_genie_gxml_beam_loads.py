"""GeniE itself as the oracle for the concept XML adapy writes: loads on beams, and the workspace
GeniE saves after meshing.

The model is the one the loads investigation compared against GeniE's own: two IPE300 beams at
y=0 and y=1.5 beside a 4 x 1 m plate at y=3..4. adapy writes it (with the embedded ACIS body,
the default), headless GenieRuntime imports it, meshes it at 0.5 m and exports ``T1.FEM``; the
load records in that deck are summed per load case and compared with what GeniE writes for the
same loads on its own model.

* **Beam loads.** The SAT body box used to be the plate's extent only, while the beams hang off
  the same shell as wires outside it. GeniE then placed no load that finds a beam by position:
  load cases 1, 4 and 5 below wrote no records at all. The plate load (case 3) is the control.
* **Meshed workspace.** Meshing adds a ``mirror_model`` of nameless property references that
  ``from_gnx`` used to crash on.
"""

from __future__ import annotations

import collections
import xml.etree.ElementTree as ET

import numpy as np
import pytest

import ada
from ada.fem.concept.loads import (
    LoadConceptAccelerationField,
    LoadConceptCase,
    LoadConceptCaseCombination,
    LoadConceptCaseFactored,
    LoadConceptLine,
    LoadConceptPoint,
)


def _genie_runtime():
    from ada.fem.formats.sesam.sesam_exe_locator import (
        get_genie_runtime_default_exe_path,
    )

    try:
        return get_genie_runtime_default_exe_path()
    except Exception:  # noqa: BLE001 - any locator failure is "not installed" here
        return None


pytestmark = pytest.mark.skipif(_genie_runtime() is None, reason="GeniE is not installed")

#: GeniE V9.2-01's own records for these loads on its own beams and plate (the investigation's
#: ``all1`` model): the summed force and moment per load case, and the record kind.
GENIE_NATIVE = {
    "LC_pt_beam": ("BNLOAD", (0, 0, -10000), (0, 500, 0)),
    "LC_pt_plate": ("BNLOAD", (0, 0, -5000), (0, 0, 0)),
    "LC_ll_beam_u": ("BELOAD1", (0, 0, -4000), (0, 0, 0)),
    "LC_ll_beam_v": ("BELOAD1", (0, 0, -8000), (0, 0, 0)),
}


def _model() -> ada.Assembly:
    bm1 = ada.Beam("Bm1", (0, 0, 0), (4, 0, 0), sec="IPE300")
    bm2 = ada.Beam("Bm2", (0, 1.5, 0), (4, 1.5, 0), sec="IPE300")
    pl1 = ada.Plate.from_3d_points("Pl1", [(0, 3, 0), (4, 3, 0), (4, 4, 0), (0, 4, 0)], 0.01)
    p = ada.Part("P") / (bm1, bm2, pl1)
    loads = p.concept_fem.loads

    def lc(name, n, items):
        return loads.add_load_case(LoadConceptCase(name, loads=items, fem_loadcase_number=n))

    lc(
        "LC_pt_beam",
        1,
        [
            LoadConceptPoint("PL_beam", (2, 0, 0), (0, 0, -10000), (0, 0, 0)),
            LoadConceptPoint("PL_beam_mom", (1, 0, 0), (0, 0, 0), (0, 500, 0)),
        ],
    )
    lc("LC_pt_plate", 3, [LoadConceptPoint("PL_plate", (2, 3.5, 0), (0, 0, -5000), (0, 0, 0))])
    lc_u = lc("LC_ll_beam_u", 4, [LoadConceptLine("LL_beam_u", (0, 0, 0), (4, 0, 0), (0, 0, -1000), (0, 0, -1000))])
    lc("LC_ll_beam_v", 5, [LoadConceptLine("LL_beam_v", (0, 1.5, 0), (4, 1.5, 0), (0, 0, -1000), (0, 0, -3000))])
    lc_g = lc("LC_grav", 16, [LoadConceptAccelerationField("G", (0, 0, -9.80665))])
    loads.add_load_case_combination(
        LoadConceptCaseCombination(
            "LCC1", load_cases=[LoadConceptCaseFactored(lc_u, 1.5), LoadConceptCaseFactored(lc_g, 1.0)]
        )
    )
    return ada.Assembly("A") / p


def _records(fem_path) -> list[tuple[str, list]]:
    """Free-format Sesam records: an 8-character name, then fields; continuation lines start blank."""
    recs, cur = [], None
    for line in open(fem_path):
        if not line.strip():
            continue
        if line[0] != " ":
            if cur:
                recs.append(cur)
            cur = [line[:8].strip(), line[8:]]
        else:
            cur[1] += line[8:]
    if cur:
        recs.append(cur)
    out = []
    for name, body in recs:
        if name == "TDLOAD":
            out.append((name, body.split("\n")))
            continue
        fields = []
        for tok in body.split():
            try:
                fields.append(float(tok))
            except ValueError:
                fields.append(tok)
        out.append((name, fields))
    return out


def _load_summary(fem_path) -> dict[str, tuple[collections.Counter, np.ndarray, np.ndarray]]:
    """Per load-case name: record kinds, summed force, summed nodal moment.

    ``BELOAD1`` (SIF 89-7012 section 7.2.7) is integrated as linear between the intensities at the
    loaded length's ends, which is exact for the constant and linearly varying loads used here.
    """
    recs = _records(fem_path)
    coord, nodes_of, lc_name = {}, {}, {}
    for name, v in recs:
        if name == "GCOORD":
            coord[int(v[0])] = np.asarray(v[1:4])
        elif name == "GELMNT1":
            nodes_of[int(v[0])] = [int(x) for x in v[4:]]
        elif name == "TDLOAD":
            lc_name[int(float(v[0].split()[1]))] = v[1].strip()
    out = collections.defaultdict(lambda: [collections.Counter(), np.zeros(3), np.zeros(3)])
    for name, v in recs:
        if name == "BNLOAD":
            nd = int(v[5])
            comps = np.asarray(v[6 : 6 + nd])
            entry = out[lc_name[int(v[0])]]
            entry[0][name] += 1
            entry[1] += comps[:3]
            entry[2] += comps[3:6]
        elif name == "BELOAD1":
            el, l1, l2, nd = int(v[4]), v[5], v[6], int(v[7])
            q = np.asarray(v[9 : 9 + nd]).reshape(-1, 3)
            ns = nodes_of[el]
            loaded = np.linalg.norm(coord[ns[-1]] - coord[ns[0]]) - l1 - l2
            entry = out[lc_name[int(v[0])]]
            entry[0][name] += 1
            entry[1] += 0.5 * (q[0] + q[-1]) * loaded
    return {k: tuple(v) for k, v in out.items()}


@pytest.fixture(scope="module")
def genie_run(tmp_path_factory):
    """adapy writes the model; GeniE imports, re-exports, meshes, exports the deck and saves."""
    from ada.cadit.gxml.open_in_genie import verify_genie_import

    out = tmp_path_factory.mktemp("genie_beam_loads")
    xml_file = out / "model.xml"
    _model().to_genie_xml(xml_file)
    o = out.as_posix()
    extra_js = f"""ExportConceptXml().DoExport("{o}/after_import.xml");
Md = MeshDensity(0.5 m);
Md.setDefault();
Analysis1 = Analysis(true);
Analysis1.add(MeshActivity());
Analysis1.setActive();
Analysis1.execute();
GenieRules.Meshing.superElementType = 1;
ExportMeshFem().DoExport("{o}/T1.FEM");
Save();
"""
    ws = out / "ws" / "beam_loads"
    # licenses="" leaves the runtime's own licence selection: meshing needs more than the
    # CurvedGeometry feature an import-only check asks for
    result = verify_genie_import(xml_file, workspace=ws, licenses="", extra_js=extra_js, timeout=900)
    assert result.success, (result.error_kind, result.error_detail, result.stdout[-2000:])
    assert (out / "T1.FEM").is_file(), result.stdout[-2000:]
    return out, ws.parent / f"{ws.name}.gnx"


def test_the_sat_body_is_embedded(genie_run):
    """Not vacuous: the beams reference edges in an embedded ACIS body, the path that failed."""
    out, _ = genie_run
    root = ET.parse(out / "model.xml").getroot()
    assert root.find("./model/structure_domain/geometry/sat_embedded_sequence") is not None
    edges = [e.attrib["edge_ref"] for e in root.iter("edge") if "edge_ref" in e.attrib]
    assert len(edges) == 2


@pytest.mark.parametrize("lc_name", sorted(GENIE_NATIVE))
def test_every_load_lands_with_genies_resultant(genie_run, lc_name):
    out, _ = genie_run
    summary = _load_summary(out / "T1.FEM")
    assert lc_name in summary, f"GeniE wrote no load records for {lc_name}"
    kinds, force, moment = summary[lc_name]
    kind, f_ref, m_ref = GENIE_NATIVE[lc_name]
    assert set(kinds) == {kind}
    assert force == pytest.approx(f_ref, abs=1e-6)
    assert moment == pytest.approx(m_ref, abs=1e-6)


def test_from_gnx_reads_the_workspace_genie_meshed(genie_run):
    _, gnx = genie_run
    a = ada.from_gnx(gnx)
    assert sorted(bm.name for bm in a.get_all_physical_objects(by_type=ada.Beam)) == ["Bm1", "Bm2"]
    assert [pl.name for pl in a.get_all_physical_objects(by_type=ada.Plate)] == ["Pl1"]
