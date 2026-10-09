"""GeniE itself as the oracle for the supports adapy reads from its XML and writes back.

Each fixture (GeniE V8.13-02 exports of the support probe models, see
``tests/core/cadit/gxml/test_gxml_read_supports.py``) is imported into headless GenieRuntime twice:
as GeniE wrote it, and as adapy writes it after reading it. Both are meshed at 0.5 m and exported
to ``T1.FEM``, and the support records of the two decks are compared node by node, by position:

* BNBCD fixity codes, turned into global axes through the node's BNDOF/BNTRCOS where GeniE gave it
  one (adapy writes every support in global axes, GeniE in the support's own frame -- the same
  physics, different records);
* the diagonal of the MGSPRNG stiffness of each spring-to-ground element (ELTYP 18), per global axis;
* BLDEP: each linked node's master and its number of terms (9 translations only, 12 with
  rotations, 6 with a slave translation left free);
* BNDISPL: the prescribed values per load case and node.

The supports the reader refuses are absent from adapy's XML, so their nodes are left out of the
comparison (``REFUSED_AT``), as are the plate corners where two support curves meet: GeniE keeps
one support's codes there, and which one depends on the order it meets them in.

GeniE's version: ``ADA_GENIE_RUNTIME_EXE`` if set, else the registered default. The reference run
was V8.13-02, which every fixture came from.
"""

from __future__ import annotations

import collections
import pathlib
import shutil

import numpy as np
import pytest

import ada


def _genie_runtime():
    from ada.fem.formats.sesam.sesam_exe_locator import (
        get_genie_runtime_default_exe_path,
    )

    try:
        return get_genie_runtime_default_exe_path()
    except Exception:  # noqa: BLE001 - any locator failure is "not installed" here
        return None


pytestmark = pytest.mark.skipif(_genie_runtime() is None, reason="GeniE is not installed")

#: Positions whose records come from a support the reader refuses (so adapy does not write it),
#: or from two supports meeting at one node. Rounded as ``_key`` rounds.
REFUSED_AT = {
    "genie_supports_all_kinds.xml": {
        "points": [(0, 8, 0)],  # Sp_local, 45 degrees
        "lines": [((0, 11, 0), (4, 11, 0))],  # Sc_local, stored frame not the guide frame
        "boxes": [],
    },
    "genie_supports_frames.xml": {
        "points": [(4, 7, 0), (0, 0, 0), (4, 0, 0), (0, 1, 0), (4, 1, 0)],  # Sp_presc_rot; Pl1's corners
        "lines": [((8, 0, 0), (11, 0, 4))],  # Sc_slope
        "boxes": [((0, 12, -0.1), (2, 13, 1))],  # Srl_sdx's linked nodes and its master
    },
}

MESH_JS = """Md = MeshDensity(0.5 m);
Md.setDefault();
Analysis1 = Analysis(true);
Analysis1.add(MeshActivity());
Analysis1.setActive();
Analysis1.execute();
GenieRules.Meshing.superElementType = 1;
ExportMeshFem().DoExport("{out}/T1.FEM");
"""


def _records(fem_path) -> list[tuple[str, list[float]]]:
    """Free-format Sesam records: an 8-character name, then numbers; continuation lines start blank."""
    recs = []
    for line in open(fem_path):
        if not line.strip():
            continue
        if line[0] != " ":
            recs.append([line[:8].strip(), []])
            body = line[8:]
        else:
            body = line[8:]
        for tok in body.split():
            try:
                recs[-1][1].append(float(tok))
            except ValueError:
                recs[-1][1].append(tok)
    return [(n, v) for n, v in recs]


def _key(p) -> tuple:
    return tuple(round(float(c), 3) + 0.0 for c in p)


def _is_signed_permutation(frame: np.ndarray) -> bool:
    return bool(np.allclose(np.abs(frame).max(axis=1), 1.0, atol=1e-6))


def _on_axis(frame: np.ndarray, local: list) -> list:
    """Per-local-axis values onto the global axes, for a frame that is a signed permutation."""
    out = [None] * 3
    for i, row in enumerate(frame):
        out[int(np.argmax(np.abs(row)))] = local[i]
    return out


def _supports(fem_path) -> dict:
    """The support records of a deck, in global axes, keyed by node position."""
    recs = _records(fem_path)
    coord, frames_of, trans, elnodes, elmat, springs = {}, {}, {}, {}, {}, {}
    for name, v in recs:
        if name == "GCOORD":
            coord[int(v[0])] = _key(v[1:4])
        elif name == "BNDOF":
            frames_of[int(v[0])] = int(v[2])  # NODENO, then the BNTRCOS number (twice)
        elif name == "BNTRCOS":
            trans[int(v[0])] = np.asarray(v[1:10], dtype=float).reshape(3, 3).T
        elif name == "GELMNT1" and int(v[2]) == 18:
            elnodes[int(v[1])] = int(v[4])  # by the internal number GELREF1 uses
        elif name == "GELREF1":
            elmat[int(v[0])] = int(v[1])
        elif name == "MGSPRNG":
            k = np.zeros((6, 6))
            k[np.triu_indices(6)[::-1]] = v[2:23]  # the lower triangle, column by column
            springs[int(v[0])] = np.diag(k)

    def to_global(node, values6):
        frame = trans[frames_of[node]] if node in frames_of else np.eye(3)
        if not _is_signed_permutation(frame):  # only at a support the reader refuses
            return ("rotated frame", tuple(values6), tuple(np.round(frame, 3).ravel()))
        return tuple(_on_axis(frame, list(values6[:3])) + _on_axis(frame, list(values6[3:6])))

    out = collections.defaultdict(dict)
    for name, v in recs:
        if name == "BNBCD":
            node = int(v[0])
            out[coord[node]]["BNBCD"] = to_global(node, [int(c) for c in v[2:8]])
        elif name == "BLDEP":
            slave, master, nterms = int(v[0]), int(v[1]), int(v[3])
            out[coord[slave]]["BLDEP"] = (coord[master], nterms)
        elif name == "BNDISPL":
            lc, node = int(v[0]), int(v[4])
            # a signed permutation flips the sign of a value along a reversed axis
            frame = trans[frames_of[node]] if node in frames_of else np.eye(3)
            values = np.asarray(v[6:12], dtype=float)
            glob = np.concatenate([frame.T @ values[:3], frame.T @ values[3:]])
            out[coord[node]][f"BNDISPL{lc}"] = tuple(round(float(x), 6) + 0.0 for x in glob)
    for el, node in elnodes.items():
        diag = springs[elmat[el]]
        out[coord[node]]["MGSPRNG"] = tuple(round(float(x), 3) for x in to_global(node, list(diag)))
    return dict(out)


def _excluded(key, refused) -> bool:
    p = np.asarray(key)
    if any(np.allclose(p, q, atol=1e-3) for q in refused["points"]):
        return True
    for a, b in refused["lines"]:
        a, b = np.asarray(a, float), np.asarray(b, float)
        t = np.dot(p - a, b - a) / np.dot(b - a, b - a)
        if -1e-6 <= t <= 1 + 1e-6 and np.linalg.norm(a + t * (b - a) - p) < 1e-3:
            return True
    return any(np.all(p >= np.asarray(lo) - 1e-3) and np.all(p <= np.asarray(hi) + 1e-3) for lo, hi in refused["boxes"])


def _mesh_in_genie(xml_file, out_dir, name):
    from ada.cadit.gxml.open_in_genie import verify_genie_import

    out_dir.mkdir(parents=True, exist_ok=True)
    # licenses="" leaves the runtime's own licence selection: meshing needs more than CurvedGeometry
    result = verify_genie_import(
        xml_file,
        workspace=out_dir / "ws" / name,
        licenses="",
        extra_js=MESH_JS.format(out=out_dir.as_posix()),
        timeout=900,
    )
    assert result.success, (result.error_kind, result.error_detail, result.stdout[-2000:])
    assert (out_dir / "T1.FEM").is_file(), result.stdout[-2000:]
    return _supports(out_dir / "T1.FEM")


@pytest.fixture(scope="module", params=sorted(REFUSED_AT))
def both_decks(request, tmp_path_factory):
    example_files = pathlib.Path(__file__).resolve().parents[2] / "files"
    fixture = request.param
    work = tmp_path_factory.mktemp(fixture.split(".")[0])
    src = work / fixture
    shutil.copy(example_files / "fem_files" / "sesam" / fixture, src)
    genie = _mesh_in_genie(src, work / "genie", "genie")

    a = ada.from_genie_xml(src)
    adapy_xml = work / "adapy" / "adapy.xml"
    adapy_xml.parent.mkdir()
    a.to_genie_xml(adapy_xml)
    adapy = _mesh_in_genie(adapy_xml, work / "adapy", "adapy")
    return fixture, genie, adapy


def test_genie_meshes_adapys_supports_into_the_same_records(both_decks):
    fixture, genie, adapy = both_decks
    refused = REFUSED_AT[fixture]
    kept_genie = {k: v for k, v in genie.items() if not _excluded(k, refused)}
    kept_adapy = {k: v for k, v in adapy.items() if not _excluded(k, refused)}
    # not vacuous: every record kind the fixture exercises is there
    kinds = {kind.rstrip("0123456789") for v in kept_genie.values() for kind in v}
    assert {"BNBCD", "BLDEP", "MGSPRNG", "BNDISPL"} <= kinds
    diff = {
        k: (kept_genie.get(k), kept_adapy.get(k))
        for k in sorted(set(kept_genie) | set(kept_adapy))
        if kept_genie.get(k) != kept_adapy.get(k)
    }
    assert diff == {}
    assert len(kept_genie) > 30


def test_the_refused_supports_are_where_the_comparison_leaves_out(both_decks):
    """Not vacuous: GeniE's own deck has records at every excluded place, and adapy's has none
    there but the corners, which carry a kept support's codes."""
    fixture, genie, adapy = both_decks
    refused = REFUSED_AT[fixture]
    for p in refused["points"]:
        assert _key(p) in genie, p
    for a, b in refused["lines"]:
        assert any(_excluded(k, {"points": [], "lines": [(a, b)], "boxes": []}) for k in genie)
        assert not any(_excluded(k, {"points": [], "lines": [(a, b)], "boxes": []}) for k in adapy)
    for box in refused["boxes"]:
        assert any(_excluded(k, {"points": [], "lines": [], "boxes": [box]}) for k in genie)
        assert not any(_excluded(k, {"points": [], "lines": [], "boxes": [box]}) for k in adapy)
