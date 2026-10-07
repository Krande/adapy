"""Concept supports meshed by adapy into the records GeniE meshes them into, node by node.

Each GeniE fixture (``files/fem_files/sesam/genie_supports_*.xml``, GeniE V8.13-02 exports) is read with
``from_genie_xml``, meshed by adapy at GeniE's 0.5 m and written as a Sesam deck; its support records are compared
by node position with the ``T1.FEM`` GeniE itself meshed the same model into (``*_T1.FEM``, beside it):

* BNBCD codes, in global axes through GeniE's BNTRCOS where it gave a node one;
* the MGSPRNG diagonal of each spring to ground (GSPR, element type 18);
* BLDEP: each linked node's master and number of terms (9 translations only, 12 with rotations);
* BNDISPL: the values per load case, by the case's name.

Every place the two decks differ is listed in ``DIFFERENT`` with why, and nowhere else may they: a support the GeniE
XML reader refuses (so adapy has nothing to mesh), a point with no node in adapy's mesh, a dependent dof, and the
plate corners where two support curves meet (GeniE keeps one curve's codes, adapy both).
"""

from __future__ import annotations

import collections
import pathlib
import re
import shutil

import numpy as np
import pytest

import ada
from ada.fem.formats import conversion_report

STAGE = "concept to fem"

_SLOPE = [(8.0 + 0.3 * i, 0.0, 0.4 * i) for i in range(11)]

#: Where adapy's deck differs from GeniE's, and why. Positions rounded as ``_key`` rounds.
DIFFERENT = {
    "genie_supports_frames.xml": {
        **dict.fromkeys([(0, 1, 0), (4, 0, 0), (4, 1, 0)], "plate corner: two curves, GeniE keeps one"),
        **dict.fromkeys(
            [(x, y, 0) for x in (1, 1.5, 2) for y in (12.5, 13)] + [(2, 13, 1)], "Srl_sdx: refused by the reader"
        ),
        (4, 7, 0): "Sp_presc_rot: refused by the reader",
        **dict.fromkeys(_SLOPE, "Sc_slope: refused by the reader"),
    },
    "genie_supports_all_kinds.xml": {
        (0, 8, 0): "Sp_local: refused by the reader",
        **dict.fromkeys([(0.5 * i, 11, 0) for i in range(9)], "Sc_local: refused by the reader"),
        (1.3, 1.5, 0): "Sp_mid: no node in adapy's mesh",
        (4, 16, 0): "Sp_dep: a dependent dof, refused",
    },
    "genie_supports_rigid_link_edges.xml": {},
}


def _key(p) -> tuple:
    return tuple(round(float(c), 3) + 0.0 for c in p)


def _records(fem_path) -> list[tuple[str, list]]:
    """Free-format Sesam records: an 8-character name, then numbers; continuation lines start blank."""
    recs = []
    for line in open(fem_path):
        if not line.strip():
            continue
        if line[0] != " ":
            recs.append([line[:8].strip(), []])
        for tok in line[8:].split():
            try:
                recs[-1][1].append(float(tok))
            except ValueError:
                recs[-1][1].append(tok)
    return recs


def _on_axes(frame: np.ndarray, local) -> list:
    out = [None] * 3
    for i, row in enumerate(frame):
        out[int(np.argmax(np.abs(row)))] = local[i]
    return out


def supports(fem_path) -> dict:
    """The support records of a deck in global axes, keyed by node position. An all-free BNBCD says nothing and is
    left out."""
    recs = _records(fem_path)
    text = pathlib.Path(fem_path).read_text()
    names = {int(float(m[1])): m[2] for m in re.finditer(r"^TDLOAD\s+\S+\s+(\S+).*\n\s+(\S+)", text, re.M)}
    coord, frames_of, trans, elnodes, elmat, springs = {}, {}, {}, {}, {}, {}
    for name, v in recs:
        if name == "GCOORD":
            coord[int(v[0])] = _key(v[1:4])
        elif name == "BNDOF":
            frames_of[int(v[0])] = int(v[2])
        elif name == "BNTRCOS":
            trans[int(v[0])] = np.asarray(v[1:10], dtype=float).reshape(3, 3).T
        elif name == "GELMNT1" and int(v[2]) == 18:
            elnodes[int(v[1])] = int(v[4])
        elif name == "GELREF1":
            elmat[int(v[0])] = int(v[1])
        elif name == "MGSPRNG":
            k = np.zeros((6, 6))
            k[np.triu_indices(6)[::-1]] = v[2:23]
            springs[int(v[0])] = np.diag(k)

    def frame(node):
        return trans[frames_of[node]] if node in frames_of else np.eye(3)

    def to_global(node, values6):
        f = frame(node)
        if not np.allclose(np.abs(f).max(axis=1), 1.0, atol=1e-6):
            return ("rotated frame", tuple(values6))
        return tuple(_on_axes(f, list(values6[:3])) + _on_axes(f, list(values6[3:6])))

    out = collections.defaultdict(dict)
    for name, v in recs:
        if name == "BNBCD":
            codes = [int(c) for c in v[2:8]]
            if any(codes):
                out[coord[int(v[0])]]["BNBCD"] = to_global(int(v[0]), codes)
        elif name == "BLDEP":
            out[coord[int(v[0])]]["BLDEP"] = (coord[int(v[1])], int(v[3]))
        elif name == "BNDISPL":
            node, values = int(v[4]), np.asarray(v[6:12], dtype=float)
            glob = np.concatenate([frame(node).T @ values[:3], frame(node).T @ values[3:]])
            out[coord[node]][f"BNDISPL {names[int(v[0])]}"] = tuple(round(float(x), 6) + 0.0 for x in glob)
    for el, node in elnodes.items():
        out[coord[node]]["MGSPRNG"] = tuple(round(float(x), 3) for x in to_global(node, list(springs[elmat[el]])))
    return dict(out)


def mesh_and_write(xml: pathlib.Path, work: pathlib.Path):
    shutil.copy(xml, work / xml.name)
    with conversion_report.collect() as report:
        a = ada.from_genie_xml(work / xml.name)
        (part,) = a.get_all_subparts()
        part.fem = part.to_fem_obj(0.5, use_quads=True)
        a.to_fem("adapy", "sesam", scratch_dir=work, overwrite=True)
    return a, part, next((work / "adapy").glob("*T1.FEM")), report


@pytest.fixture(scope="module", params=sorted(DIFFERENT))
def decks(request, tmp_path_factory):
    files = pathlib.Path(__file__).resolve().parents[3] / "files" / "fem_files" / "sesam"
    xml = files / request.param
    _, part, deck, report = mesh_and_write(xml, tmp_path_factory.mktemp(xml.stem))
    return request.param, supports(files / f"{xml.stem}_T1.FEM"), supports(deck), part, report


def test_adapy_meshes_the_supports_into_genies_records(decks):
    fixture, genie, adapy, _, _ = decks
    diff = {k for k in set(genie) | set(adapy) if genie.get(k) != adapy.get(k)}
    expected = {_key(p) for p in DIFFERENT[fixture]}
    assert sorted(diff - expected) == [], {k: (genie.get(k), adapy.get(k)) for k in sorted(diff - expected)}
    assert sorted(expected - diff) == [], "a listed difference is gone: update DIFFERENT"
    assert len(set(genie) - diff) > 25


@pytest.mark.parametrize(
    "fixture, kinds",
    [
        ("genie_supports_frames.xml", {"BNBCD", "BLDEP", "MGSPRNG", "BNDISPL LC1", "BNDISPL LC2"}),
        ("genie_supports_all_kinds.xml", {"BNBCD", "BLDEP", "MGSPRNG", "BNDISPL LC_presc"}),
        ("genie_supports_rigid_link_edges.xml", {"BNBCD", "BLDEP"}),
    ],
)
def test_the_comparison_is_not_vacuous(decks, fixture, kinds):
    """Every record kind the fixture's GeniE deck holds is among those compared equal."""
    name, genie, adapy, _, _ = decks
    if name != fixture:
        pytest.skip("another fixture")
    same = {k for k in genie if genie[k] == adapy.get(k)}
    assert kinds <= {kind for k in same for kind in genie[k]}


#: The values GeniE wrote, by node: spring diagonals, a per-length curve spring, settlements per case, retained
#: dofs and rotation-dependent links. Read off GeniE's decks; adapy must write the same.
GENIE_VALUES = {
    "genie_supports_all_kinds.xml": {
        (0, 6, 0): {"MGSPRNG": (0.0, 0.0, 1e6, 0.0, 2e5, 0.0)},
        (0, 6, 4): {"MGSPRNG": (0.0, 2e6, 0.0, 0.0, 0.0, 0.0)},
        (0, 10, 0): {"MGSPRNG": (0.0, 0.0, 125000.0, 0.0, 0.0, 0.0)},
        (2, 10, 0): {"MGSPRNG": (0.0, 0.0, 250000.0, 0.0, 0.0, 0.0)},
        (4, 10, 0): {"MGSPRNG": (0.0, 0.0, 125000.0, 0.0, 0.0, 0.0)},
        (4, 8, 0): {"BNBCD": (1, 1, 2, 0, 0, 0), "BNDISPL LC_presc": (0.0, 0.0, -0.01, 0.0, 0.0, 0.0)},
        (0, 16, 0): {"BNBCD": (4, 4, 4, 4, 4, 4)},
    },
    "genie_supports_frames.xml": {
        (4, 8, 0): {"MGSPRNG": (0.0, 1e6, 0.0, 0.0, 0.0, 0.0)},
        (14, 0, 0): {
            "BNBCD": (2, 1, 2, 0, 0, 2),
            "BNDISPL LC1": (-0.003, 0.0, 0.0, 0.0, 0.0, 0.0),
            "BNDISPL LC2": (0.005, 0.0, -0.01, 0.0, 0.0, 0.001),
        },
        (1, 9.5, 0): {"BNBCD": (3, 3, 3, 3, 3, 3), "BLDEP": ((2, 10, 1), 12)},
        (1, 21.5, 0): {"BNBCD": (3, 3, 3, 0, 0, 0), "BLDEP": ((2, 22, 1), 9)},
    },
    "genie_supports_rigid_link_edges.xml": {
        (3, 34, 0): {"BNBCD": (3, 3, 3, 0, 0, 0), "BLDEP": ((2, 33.5, 1), 9)},
        (2, 33.5, 1): {"BNBCD": (1, 1, 1, 1, 1, 1)},
    },
}


def test_the_values_are_genies(decks):
    fixture, genie, adapy, _, _ = decks
    for p, records in GENIE_VALUES[fixture].items():
        want = {k: (_key(v[0]), v[1]) if k == "BLDEP" else v for k, v in records.items()}
        assert {k: genie[_key(p)][k] for k in want} == want, f"the oracle at {p}"
        assert {k: adapy.get(_key(p), {}).get(k) for k in want} == want, p


def _findings(report) -> dict[str, list[str]]:
    out = collections.defaultdict(list)
    for f in report.findings:
        if f.stage == STAGE:
            for subject in [f.subject, *f.other_subjects]:
                out[subject].append(f.kind)
    return dict(out)


def test_what_is_not_meshed_is_reported_by_name(decks):
    fixture, _, _, _, report = decks
    found = _findings(report)
    expected = {
        "genie_supports_all_kinds.xml": {
            "Sp_dep.dx": ["omitted"],
            "Sp_mid": ["omitted"],
            "Sp_super": ["note"],
        },
        "genie_supports_frames.xml": {"Srl_only": ["omitted"], "Sc_y_const": ["suspect"]},
        "genie_supports_rigid_link_edges.xml": {
            n: ["omitted"] for n in ("Srl_A", "Srl_B", "Srl_C", "Srl_D", "Srl_F", "Srl_G")
        },
    }[fixture]
    for subject, kinds in expected.items():
        assert found.get(subject) == kinds, (subject, found)


def test_include_all_edges_false_links_nothing_as_in_genie(decks):
    """GeniE V8.13-02 linked no node for any of six links with include_all_edges false -- over a whole plate, a
    whole plate edge, a whole beam, part of a beam, and a box holding a support point -- and wrote no master; the
    three twins with it true linked every node in the box. adapy makes no coupling either."""
    fixture, genie, adapy, part, _ = decks
    if fixture != "genie_supports_rigid_link_edges.xml":
        pytest.skip("another fixture")
    links = part.concept_fem.constraints.rigid_links
    assert sorted(n for n, rl in links.items() if not rl.include_all_edges) == [
        "Srl_A",
        "Srl_B",
        "Srl_C",
        "Srl_D",
        "Srl_F",
        "Srl_G",
    ]
    assert sorted(part.fem.constraints) == ["Srl_A2", "Srl_B2", "Srl_D2"]
    masters = sorted({v["BLDEP"][0] for v in genie.values() if "BLDEP" in v})
    assert masters == [(1.25, 39.5, 1.0), (2.0, 33.5, 1.0), (9.0, 30.5, 1.0)]


def test_tributary_lengths_spread_a_curve_spring_as_genie_does():
    from ada.fem.concept.to_fem import tributary_lengths

    nodes = [ada.Node((x, 10, 0)) for x in (0.5, 0.0, 4.0, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5)]
    shares = tributary_lengths(nodes, (0, 10, 0), (4, 10, 0))
    # 500000 N/m^2: GeniE's MGSPRNG 125000 at the end nodes, 250000 inside
    assert [500000 * s for s in shares] == [250000.0, 125000.0, 125000.0] + [250000.0] * 6
    assert sum(shares) == pytest.approx(4.0)


def test_a_prescribed_dof_is_held_and_valued_per_load_case(decks):
    fixture, _, _, part, _ = decks
    if fixture != "genie_supports_frames.xml":
        pytest.skip("another fixture")
    from ada.fem.constraints import BC_LOAD_CASE

    bcs = {bc.name: bc for bc in part.fem.bcs if bc.name.startswith("Sp_presc")}
    assert sorted(bcs) == ["Sp_presc", "Sp_presc_LC1", "Sp_presc_LC2"]
    assert bcs["Sp_presc"].dofs == [1, 2, 3, 6] and set(bcs["Sp_presc"].magnitudes) == {None}
    assert (bcs["Sp_presc_LC1"].dofs, bcs["Sp_presc_LC1"].magnitudes) == ([1, 3, 6], [-0.003, 0.0, 0.0])
    assert (bcs["Sp_presc_LC2"].dofs, bcs["Sp_presc_LC2"].magnitudes) == ([1, 3, 6], [0.005, -0.01, 0.001])
    assert [bcs[n].metadata[BC_LOAD_CASE] for n in ("Sp_presc_LC1", "Sp_presc_LC2")] == ["LC1", "LC2"]
