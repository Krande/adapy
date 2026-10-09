"""A synthetic SIN result deck for the lazy load-combination tests.

Built from scratch with :mod:`ada.fem.formats.sesam.results.sin_writer`, so it
carries no solver output and no model of anyone's: a flat plate meshed with
four-node shells and, over a strip of it, three-node shells whose diagonals meet
eight to a node (so the shell nodal average sums eight or more contributors); a
web plate standing on the plate's centre line (a T-junction, where the averaging
gives up because the normals disagree); a row of beams along one edge; reaction
records along another. Result cases: real basic cases, complex basic cases (real
and imaginary words interleaved), and RDRESCMB combinations of them that the
file does NOT store -- real ones, ones with a phase on a real case, ones with a
complex case at zero phase, ones with a complex case at a non-zero phase (which
need the raw records), complex combinations, and one that names the same basic
case twice at two phases.

Values are smooth patterns times random amplitudes, rounded to float32 by the
writer like any SIN word, with stresses of order 1e7-1e8 so float32 rounding in
the superposition is real.
"""

from __future__ import annotations

import math
import pathlib
from dataclasses import dataclass, field

import numpy as np

from ada.fem.formats.sesam.results.sin_writer import SinDeck, write_sin

QUAD, TRI, BEAM = 24, 25, 15
QUAD_NSP, TRI_NSP, BEAM_NSP = 10, 8, 3


@dataclass
class SyntheticDeck:
    path: pathlib.Path
    basics: list[int]
    complex_basics: list[int]
    combinations: dict[int, tuple[bool, list[tuple[int, float, float]]]]
    n_nodes: int
    n_shells: int
    n_beams: int
    names: dict[int, str] = field(default_factory=dict)


def _rdpoints_shell(elem: int, eltyp: int, nsp: int) -> list[float]:
    # ispalt, iielno, icoref, ieltyp, nsp, ijkdim, nsptra, nlay, then per point
    # (id, xi, eta, zeta) with a -1 marker, then the 3x3 transformation. Only
    # nsp and ieltyp are read; the rest is a plausible filler.
    words = [1, elem, eltyp, eltyp, nsp, 20303 if eltyp == QUAD else 20202, 1, 0]
    half = nsp // 2
    for k in range(nsp):
        layer = -0.5 if k < half else 0.5
        words += [k + 1, 0.1 * (k % half), 0.05 * (k % 2), layer, -1]
    words = words[:-1]
    words += [1, 0, 0, 0, 1, 0, 0, 0, 1]
    return words


def _rdpoints_beam(elem: int) -> list[float]:
    return [1, elem, BEAM, BEAM, BEAM_NSP, 10103, 1, 0, 1, 0, 0.5, 0.5, 2, 0.5, 0.5, 0.5, 3, 1.0, 0.5, 0.5] + [
        1,
        0,
        0,
        0,
        1,
        0,
        0,
        0,
        1,
    ]


def build_synthetic_deck(
    path: pathlib.Path,
    *,
    nx: int = 16,
    ny: int = 10,
    n_tri_cols: int = 4,
    web_rows: int = 3,
    n_real: int = 5,
    n_complex: int = 2,
    n_combinations: int = 14,
    seed: int = 7,
) -> SyntheticDeck:
    """Write the deck to ``path``. ``n_tri_cols`` columns of the plate are three-node shells."""
    rng = np.random.default_rng(seed)
    deck = SinDeck()
    dx, dy, dz = 1.0, 0.8, 0.7

    # ── nodes ──────────────────────────────────────────────────────────
    coords: dict[int, tuple[float, float, float]] = {}

    def plate_node(i: int, j: int) -> int:
        return 1 + i * (ny + 1) + j

    for i in range(nx + 1):
        for j in range(ny + 1):
            coords[plate_node(i, j)] = (i * dx, j * dy, 0.0)
    jw = ny // 2
    nx_web = nx - n_tri_cols
    next_node = plate_node(nx, ny) + 1
    web_node: dict[tuple[int, int], int] = {}
    for i in range(nx_web + 1):
        web_node[(i, 0)] = plate_node(i, jw)
        for k in range(1, web_rows + 1):
            web_node[(i, k)] = next_node
            coords[next_node] = (i * dx, jw * dy, -k * dz)
            next_node += 1
    node_ids = sorted(coords)
    for n in node_ids:
        deck.add("GNODE", [n, n, 6, 123456])
        x, y, z = coords[n]
        deck.add("GCOORD", [n, x, y, z])

    # ── elements ───────────────────────────────────────────────────────
    shells: list[tuple[int, int, list[int], int]] = []  # (elem, eltyp, nodes, geono)
    elem = 1
    for i in range(nx):
        for j in range(ny):
            a, b, c, d = plate_node(i, j), plate_node(i + 1, j), plate_node(i + 1, j + 1), plate_node(i, j + 1)
            if i < nx - n_tri_cols:
                shells.append((elem, QUAD, [a, b, c, d], 1))
                elem += 1
                continue
            # Split on the diagonal through the corner whose indices are both odd:
            # every such node then has two triangles from each of its four squares.
            if (i % 2, j % 2) in {(1, 1), (0, 0)}:
                tris = [[a, b, c], [a, c, d]]  # diagonal a-c
            else:
                tris = [[a, b, d], [b, c, d]]  # diagonal b-d
            for t in tris:
                shells.append((elem, TRI, t, 1))
                elem += 1
    for i in range(nx_web):
        for k in range(web_rows):
            n = [web_node[(i, k)], web_node[(i + 1, k)], web_node[(i + 1, k + 1)], web_node[(i, k + 1)]]
            shells.append((elem, QUAD, n, 2))
            elem += 1
    beams: list[tuple[int, list[int]]] = []
    for i in range(nx):
        beams.append((elem, [plate_node(i, 0), plate_node(i + 1, 0)]))
        elem += 1

    for e, eltyp, nodes, geono in shells:
        deck.add("GELMNT1", [e, e, eltyp, 0, *nodes])
        deck.add("GELREF1", [e, 1, 0, 0, 0, 0, 0, 0, geono, 0, 0, 0])
    for e, nodes in beams:
        deck.add("GELMNT1", [e, e, BEAM, 0, *nodes])
        deck.add("GELREF1", [e, 1, 0, 0, 0, 0, 0, 0, 10, 0, 0, 1])
    deck.add("GELTH", [1, 0.012, 0])
    deck.add("GELTH", [2, 0.010, 0])
    deck.add("MISOSEL", [1, 2.1e11, 0.3, 7850.0, 0.03, 1.2e-5, 1, 3.55e8])
    deck.add_text("TDMATER", 1, "steel")
    deck.add(
        "GBEAMG",
        [10, 0, 8.0678e-03, 4.8663e-07, 2.1876e-04, 1.3142e-05, 0, 3.6046e-05, 1.0938e-03, 1.4602e-04]
        + [3.1457e-03, 7.7400e-04, 0, 0, 2.4307e-03, 1.1280e-04],
    )
    deck.add("GIORH", [10, 0.4, 0.0086, 0.18, 0.0135, 0.18, 0.0135, 1, 1, 0, 0, 0])
    deck.add_text("TDSECT", 10, "I400")
    deck.add("GUNIVEC", [1, 0, 0, 1])
    supports = [plate_node(0, j) for j in range(ny + 1)]
    for n in supports:
        deck.add("BNBCD", [n, 6, 1, 1, 1, 1, 1, 1])

    # ── result definitions ─────────────────────────────────────────────
    for e, eltyp, _, _ in shells:
        deck.add("RDPOINTS", _rdpoints_shell(e, eltyp, QUAD_NSP if eltyp == QUAD else TRI_NSP))
    for e, _ in beams:
        deck.add("RDPOINTS", _rdpoints_beam(e))
    deck.add("RDSTRESS", [24, 3, 1, 2, 4])
    deck.add("RDFORCES", [15, 6, 1, 2, 3, 10, 11, 12])
    deck.add("RDIELCOR", [24, 0, -1, 1, -1, 0, 1, -1, 0, 1])
    deck.add("RDIELCOR", [25, 0, 0, 1, 0, 0, 1, 0, 0, 1])
    deck.add("RDIELCOR", [15, 0, 0, 0, -1, 0, 1])
    deck.add("RDNODREA", [63, 6, 1, 2, 3, 4, 5, 6])
    deck.add("RDNODBOC", [31, 6, 1, 1, 1, 1, 1, 1])

    # ── cases ──────────────────────────────────────────────────────────
    basics = list(range(1, n_real + 1))
    complex_basics = list(range(n_real + 1, n_real + n_complex + 1))
    all_basics = basics + complex_basics
    names = {b: f"lc{b}" for b in basics}
    names.update({b: f"wave{b}" for b in complex_basics})

    xyz = np.array([coords[n] for n in node_ids])

    def pattern(case: int, salt: int, shape) -> np.ndarray:
        """Smooth in the entity index plus noise, scaled by a per-case amplitude."""
        n = int(np.prod(shape))
        t = np.linspace(0.0, 1.0, n)
        a = rng.uniform(0.3, 1.7) * (1 if rng.random() < 0.7 else -1)
        ph = rng.uniform(0, math.pi)
        smooth = np.sin(7.0 * t + ph + 0.37 * salt) + 0.4 * np.cos(23.0 * t + 0.11 * case)
        return (a * smooth + 0.05 * rng.standard_normal(n)).reshape(shape)

    def interleave(re: np.ndarray, im: np.ndarray) -> np.ndarray:
        out = np.empty(re.shape[:-1] + (2 * re.shape[-1],), dtype=np.float64)
        out[..., 0::2] = re
        out[..., 1::2] = im
        return out

    for b in all_basics:
        is_cx = b in complex_basics
        deck.add("RDRESREF", [b, b, 1, 6 if is_cx else 0, 1 if is_cx else 0, 1, 10, 1, 0])

    for b in all_basics:
        is_cx = b in complex_basics

        def values(salt: int, shape, scale: float) -> np.ndarray:
            re = pattern(b, salt, shape) * scale
            if not is_cx:
                return re
            return interleave(re, pattern(b, salt + 100, shape) * scale)

        # Displacements: rotations an order smaller; a little structure in x.
        disp = values(1, (len(node_ids), 6), 2.5e-3) * np.repeat(
            np.concatenate([np.ones(3), 0.1 * np.ones(3)]), 2 if is_cx else 1
        )
        disp *= np.repeat((1.0 + xyz[:, :1] / max(1.0, nx * dx)), disp.shape[1], axis=1)
        for n, row in zip(node_ids, disp):
            deck.add("RVNODDIS", [b, n, 6, 0, *row])
        reac = values(2, (len(supports), 6), 3.0e4)
        for n, row in zip(supports, reac):
            deck.add("RVNODREA", [b, n, 63, 31, 0, *row])
        stress_quads = values(3, (len(shells), QUAD_NSP * 3), 8.0e7)
        for k, (e, eltyp, _, _) in enumerate(shells):
            row = stress_quads[k]
            if eltyp == TRI:
                row = row[: TRI_NSP * 3 * (2 if is_cx else 1)]
            deck.add("RVSTRESS", [b, e, 1, 24, *row])
        forces = values(4, (len(beams), BEAM_NSP * 6), 2.0e5)
        for k, (e, _) in enumerate(beams):
            deck.add("RVFORCES", [b, e, 1, 15, *forces[k]])

    # ── combinations (not stored) ──────────────────────────────────────
    combinations: dict[int, tuple[bool, list[tuple[int, float, float]]]] = {}
    first = 100 + 1
    plans = [
        "real",
        "real",
        "real_phase",
        "complex_zero",
        "complex_phase",
        "complex_combo",
        "duplicate",
        "single",
    ]
    for k in range(n_combinations):
        n = first + k
        plan = plans[k % len(plans)]
        n_terms = int(rng.integers(2, 7))
        chosen = [int(x) for x in rng.choice(basics, size=min(n_terms, len(basics)), replace=False)]
        terms: list[tuple[int, float, float]] = []
        for c in chosen:
            f = float(np.float32(round(float(rng.uniform(-1.5, 1.6)), 2) or 1.0))
            terms.append((c, f, 0.0))
        cx = False
        if plan == "real_phase":
            b0, f0, _ = terms[0]
            terms[0] = (b0, f0, float(np.float32(0.5236)))
        elif plan == "complex_zero" and complex_basics:
            terms.insert(1, (complex_basics[0], float(np.float32(12.5)), 0.0))
        elif plan == "complex_phase" and complex_basics:
            terms.append((complex_basics[-1], float(np.float32(1.3)), float(np.float32(0.5236))))
        elif plan == "complex_combo":
            cx = True
            if complex_basics:
                terms.append((complex_basics[0], float(np.float32(0.8)), 0.0))
        elif plan == "duplicate":
            b0, f0, _ = terms[0]
            terms.append((b0, f0, float(np.float32(1.0472))))
        elif plan == "single":
            terms = terms[:1]
        combinations[n] = (cx, terms)
        names[n] = f"comb{n}"
        deck.add("RDRESCMB", [n, 1 if cx else 0, len(terms), *[w for t in terms for w in t]])
        deck.add("RDRESREF", [n, n, 1, 100, 1 if cx else 0, 1, 10, 1, 0])
    for n, name in sorted(names.items()):
        deck.add_text("TDRESREF", n, name)

    write_sin(deck, path)
    return SyntheticDeck(
        path=pathlib.Path(path),
        basics=basics,
        complex_basics=complex_basics,
        combinations=combinations,
        n_nodes=len(node_ids),
        n_shells=len(shells),
        n_beams=len(beams),
        names=names,
    )


_RV_HEAD = {"RVNODDIS": 4, "RVNODREA": 5, "RVSTRESS": 4, "RVFORCES": 4}


def build_fixture_deck(src: pathlib.Path, path: pathlib.Path, *, seed: int = 3) -> SyntheticDeck:
    """A committed SIN fixture plus extra cases and unstored combinations of them.

    The fixture's mesh, definitions and its stored case 1 are copied as they are;
    cases 2 and 3 are case 1's records scaled and perturbed, case 4 is complex
    (real and imaginary words built the same way, interleaved). Combinations
    11.. superpose them -- real, with a phase, with the complex case at zero and
    at a non-zero phase, and a complex combination.
    """
    from ada.fem.formats.sesam.results.sin_reader import open_sin

    rng = np.random.default_rng(seed)
    deck = SinDeck()
    with open_sin(str(src)) as sin:
        for name in sin.type_blocks:
            if name.startswith("TD"):
                for prefix, text in sin.iter_text_records(name):
                    deck.text.setdefault(name, []).append((prefix, text))
            elif name not in ("RDRESREF", "RDRESCMB"):
                deck.add_many(name, sin.iter_records(name))
        rdresref = [list(r) for r in sin.iter_records("RDRESREF")]

    template = rdresref[0] if rdresref else [1, 1, 1, 0, 0, 1, 10, 1, 0]
    basics = [1, 2, 3]
    complex_basics = [4]

    def perturbed(row: list, head: int) -> np.ndarray:
        values = np.asarray(row[head:], dtype=np.float64)
        return values * rng.uniform(0.4, 1.6) * (1 if rng.random() < 0.6 else -1) + rng.standard_normal(values.size) * (
            0.02 * (np.abs(values).max() or 1.0)
        )

    for name, head in _RV_HEAD.items():
        rows = [list(r) for r in deck.numeric.get(name, []) if int(r[0]) == 1]
        for case in (2, 3, 4):
            for row in rows:
                re = perturbed(row, head)
                if case in complex_basics:
                    im = perturbed(row, head)
                    vals = np.empty(2 * re.size)
                    vals[0::2], vals[1::2] = re, im
                else:
                    vals = re
                deck.add(name, [case, *row[1:head], *vals])
    for case in basics + complex_basics:
        rec = list(template)
        rec[0] = case
        rec[1] = case
        rec[4] = 1 if case in complex_basics else 0
        deck.add("RDRESREF", rec)

    f32 = lambda x: float(np.float32(x))  # noqa: E731
    combinations = {
        11: (False, [(1, f32(1.2), 0.0), (2, f32(1.1), 0.0)]),
        12: (False, [(3, f32(-0.9), 0.0), (1, f32(1.35), 0.0), (2, f32(0.7), 0.0)]),
        13: (False, [(2, f32(1.0), f32(0.5236)), (3, f32(1.1), 0.0)]),
        14: (False, [(1, f32(1.0), 0.0), (4, f32(12.5), 0.0)]),
        15: (False, [(1, f32(1.0), 0.0), (4, f32(12.5), f32(0.5236))]),
        16: (True, [(2, f32(0.8), 0.0), (4, f32(1.0), 0.0)]),
        17: (False, [(1, f32(1.0), 0.0), (1, f32(1.0), f32(1.0472))]),
    }
    names = {n: f"comb{n}" for n in combinations}
    for n, (cx, terms) in combinations.items():
        deck.add("RDRESCMB", [n, 1 if cx else 0, len(terms), *[w for t in terms for w in t]])
        rec = list(template)
        rec[0], rec[1], rec[4] = n, n, 1 if cx else 0
        deck.add("RDRESREF", rec)
        deck.add_text("TDRESREF", n, names[n])
    write_sin(deck, path)
    return SyntheticDeck(
        path=pathlib.Path(path),
        basics=basics,
        complex_basics=complex_basics,
        combinations=combinations,
        n_nodes=len(deck.numeric.get("GNODE", [])),
        n_shells=len(deck.numeric.get("GELMNT1", [])),
        n_beams=0,
        names=names,
    )


__all__ = ["SyntheticDeck", "build_fixture_deck", "build_synthetic_deck"]
