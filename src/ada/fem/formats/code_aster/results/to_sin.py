"""Code_Aster results as a Sesam Results Interface File (SIN), for tools that read Sestra's.

A SIN is the input deck the analysis was run from, plus result records. Code_Aster's ``.rmed`` holds
the mesh and the result fields; the input deck is the Sesam deck adapy writes for the same model
(``<name>T1.FEM``, :attr:`~..options.CodeAsterOptions.write_sin`). The two number the nodes alike (the
MED file by position, ``id - 1``, :mod:`..write.write_med`) but not the elements, so a MED cell is
found in the deck by its type and nodes. The result records are made the way Sestra makes them, so a
reader of Sestra's files reads these the same way:

* ``RVNODDIS``: the displacements of every node, from ``DEPL`` (global axes).
* ``RVNODREA``: the reactions at the nodes with a fixed DOF (``BNBCD`` code 1), from ``REAC_NODA``.
* ``RVSTRESS``: the stresses SIGXX, SIGYY, TAUXY of each 3- and 4-node shell at Sestra's result
  points -- every corner and the centroid, on the bottom surface (local ``z = -t/2``) and then the top
  -- in the element's local axes, from the shell forces ``EFGE_ELNO``: ``sigma = N / t + 6 M / t^2``
  at ``z = +t/2`` and ``N / t - 6 M / t^2`` at ``-t/2`` (``M = int sigma z dz``). The local axes are
  Code_Aster's (:func:`shell_frame`) and ``RDPOINTS`` carries them, as Sestra's carries its own.
* ``RVFORCES``: the section forces of each beam at its start, middle and end, in its local axes,
  from ``EFGE_ELNO`` (:data:`BEAM_SIGNS`). Code_Aster gives them at the two ends; the middle is
  interpolated from them with the shear (:func:`beam_mid_forces`), exact for a uniform distributed
  load.
* ``RDRESREF``/``TDRESREF``/``RBLODCMB``: a result case per Code_Aster load case (``MACRO_ELAS_MULT``
  order number), named as the deck's comment names it, and linked to the deck's load case of that
  name.

Not written: Sestra's summaries (``RSUMLOAD``, ``RSUMREAC``, ``RSUMMASS``), the stresses of
second-order shells and of solids, and the forces of springs and trusses.

Measured against Sestra V11.3 on the same model (a bulkhead topside on a jacket, 3706 shells, 2595
beams, three load cases): displacements within 3 %, reactions within 0.1 %, the shells' membrane
stresses correlating 0.99 to 1.00 element by element, the beams' axial force and their moment about
local y within 1 to 2 %. A DNV-RP-C201 buckling check of the stiffened plating that reads Sestra's SIN,
run on this SIN and on Sestra's, gives the same 1722 checks, all passing, utilisations correlating 0.97
to 1.00 check by check and the highest within 7 %.
"""

from __future__ import annotations

import os
import pathlib
import re
from dataclasses import dataclass

import h5py
import numpy as np

from ada.config import logger
from ada.fem.formats.code_aster.common import med_cell_values
from ada.fem.formats.sesam.read.deck_records import DeckRecords, read_deck_records

#: Sesam element types: the beam, and the 4- and 3-node flat shells.
BEAS, FQUS, FTRS = 15, 24, 25

#: MED cell type of each Sesam element type converted.
_MED_TYPE = {BEAS: "SE2", FQUS: "QU4", FTRS: "TR3"}

#: Sestra's result points per surface, as indices into the element's corners; -1 is the centroid
#: (measured on Sestra V11.3 SINs: FQUS ``n1 n2 c n4 n3``, FTRS ``n1 n2 c n3``).
_SURFACE_POINTS = {FQUS: (0, 1, -1, 3, 2), FTRS: (0, 1, -1, 2)}

#: ``RDIELCOR`` of each element type as Sestra writes it: the coordinate grid of its result points.
_RDIELCOR = {
    BEAS: [BEAS, 0.0, 0.0, 0.0, -1.0, 0.0, 1.0],
    FQUS: [FQUS, 0.0, -1.0, 1.0, -1.0, 0.0, 1.0, -1.0, 0.0, 1.0],
    FTRS: [FTRS, 0.0, -1.0, 1.0, -1.0, -1.0 / 3.0, 1.0, -1.0, 0.0, 1.0],
}
_IJKDIM = {BEAS: 10103.0, FQUS: 20303.0, FTRS: 20303.0}

#: Sesam's result-record codes: stresses SIGXX, SIGYY, TAUXY; beam forces NXX, NXY, NXZ, MXX, MXY, MXZ.
_STRESS_CODES = (1.0, 2.0, 4.0)
_FORCE_CODES = (1.0, 2.0, 3.0, 10.0, 11.0, 12.0)
#: The ids Sestra gives its displacement, reaction and boundary-condition descriptions.
_IRDVA, _IRREA, _IRBOC = 6.0, 63.0, 31.0

#: Sesam's beam force components from Code_Aster's ``EFGE_ELNO`` (``N VY VZ MT MFY MFZ``, section forces
#: in the beam's local axes at each of its nodes): the Code_Aster component and its sign. Measured
#: against Sestra V11.3, element by element at both ends: N, VZ, MT and MFZ are Sesam's NXX, NXZ, MXX and
#: MXZ (correlation 0.999 or better), VY and MFY its NXY and MXY the other way round (-0.95 and -1.00).
BEAM_SIGNS = {
    "NXX": ("N", 1.0),
    "NXY": ("VY", -1.0),
    "NXZ": ("VZ", 1.0),
    "MXX": ("MT", 1.0),
    "MXY": ("MFY", -1.0),
    "MXZ": ("MFZ", 1.0),
}

#: Which way the bending moment in a shell's local frame loads its top surface: ``sigma(+t/2) = N / t
#: + SHELL_BENDING_SIGN * 6 M / t^2``.
SHELL_BENDING_SIGN = 1.0


@dataclass
class _Mesh:
    coords: np.ndarray  # (n_nodes, 3), node id = row + 1
    cells: dict[str, tuple[np.ndarray, np.ndarray]]  # MED type -> (element ids, node ids (n, k))
    groups: dict[int, list[str]]  # element id -> its MED groups


def _locate_mesh(f: h5py.File):
    ens = f["ENS_MAA"]
    if len(ens) != 1:
        raise ValueError(f"expected one mesh in the MED file, found {len(ens)}")
    name = next(iter(ens))
    mesh = ens[name]
    if "NOE" not in mesh:
        mesh = mesh[next(iter(mesh))]
    return name, mesh


def _read_mesh(f: h5py.File) -> _Mesh:
    name, mesh = _locate_mesh(f)
    coo = mesh["NOE"]["COO"]
    dim = int(f["ENS_MAA"][name].attrs["ESP"])
    coords = np.asarray(coo[()]).reshape((int(coo.attrs["NBR"]), dim), order="F")
    if dim == 2:
        coords = np.column_stack([coords, np.zeros(len(coords))])

    family_groups: dict[int, list[str]] = {}
    fas = f.get("FAS", {}).get(name) if "FAS" in f else None
    if fas is not None and "ELEME" in fas:
        for fam in fas["ELEME"].values():
            num = int(fam.attrs["NUM"])
            if "GRO" in fam:
                raw = np.asarray(fam["GRO"]["NOM"][()])
                family_groups[num] = ["".join(chr(c) for c in row if c).strip() for row in raw.reshape(len(raw), -1)]

    cells, groups = {}, {}
    for med_type, grp in mesh["MAI"].items():
        nod = grp["NOD"]
        n = int(nod.attrs["NBR"])
        conn = np.asarray(nod[()]).reshape((n, -1), order="F").astype(np.int64)
        ids = np.asarray(grp["NUM"][()], dtype=np.int64) if "NUM" in grp else np.arange(1, n + 1)
        cells[med_type] = (ids, conn)
        if "FAM" in grp and family_groups:
            for eid, fam in zip(ids, np.asarray(grp["FAM"][()])):
                g = family_groups.get(int(fam))
                if g:
                    groups[int(eid)] = g
    return _Mesh(coords, cells, groups)


def _field_name(f: h5py.File, suffix: str) -> str | None:
    names = [n for n in f["CHA"] if n.endswith(suffix)]
    if len(names) > 1:
        raise ValueError(
            f"the MED file holds {suffix} of more than one result ({', '.join(names)}): which step's to convert is "
            "not decided; convert a deck of one static step"
        )
    return names[0] if names else None


def _nodal_steps(f: h5py.File, field: str | None, n_nodes: int) -> dict[int, np.ndarray]:
    """``{order number: (n_nodes, n_components)}`` of a nodal field, NaN where it is not defined."""
    if field is None:
        return {}
    out = {}
    profiles = f.get("PROFILS")
    for key, step in f["CHA"][field].items():
        noe = step["NOE"]
        pfl = noe.attrs["PFL"]
        data = noe[pfl]
        ncomp = len(f["CHA"][field].attrs["NOM"].decode().split())
        raw = np.asarray(data["CO"][()]).reshape((-1, ncomp), order="F")
        if pfl.decode() == "MED_NO_PROFILE_INTERNAL":
            values = raw
        else:
            values = np.full((n_nodes, ncomp), np.nan)
            values[np.asarray(profiles[pfl.decode()]["PFL"][()]) - 1] = raw
        out[int(step.attrs["NDT"])] = values
    return out


def _element_steps(f: h5py.File, field: str | None, med_type: str) -> tuple[list[str], dict[int, np.ndarray]]:
    """The components and ``{order number: (n_cells, n_nodes, n_components)}`` of an ELNO field on one cell type."""
    if field is None:
        return [], {}
    comps = f["CHA"][field].attrs["NOM"].decode().split()
    out = {}
    for step in f["CHA"][field].values():
        sup = f"NOE.{med_type}"
        if sup not in step:
            continue
        pfl = step[sup].attrs["PFL"]
        if pfl.decode() != "MED_NO_PROFILE_INTERNAL":
            raise NotImplementedError(f"{field} on part of the {med_type} cells (MED profile {pfl.decode()!r})")
        data = step[sup][pfl]
        n, nga = int(data.attrs["NBR"]), int(data.attrs["NGA"])
        out[int(step.attrs["NDT"])] = med_cell_values(data["CO"][()], n, nga)
    return comps, out


#: The comment adapy's writer puts above a ``MACRO_ELAS_MULT``: its load cases by order number.
_CASES_BLOCK = re.compile(r"^# Load cases of step .*?, by order number:\n((?:#   \d+: .*\n)+)", re.M)


def comm_case_names(comm: str | os.PathLike) -> dict[int, str] | None:
    """``{order number: load case name}`` of the one ``MACRO_ELAS_MULT`` of a command file, or None."""
    comm = pathlib.Path(comm)
    if not comm.exists():
        return None
    blocks = _CASES_BLOCK.findall(comm.read_text(encoding="utf-8", errors="replace"))
    if len(blocks) != 1:
        return None
    return {int(k): name.strip() for k, name in re.findall(r"^#   (\d+): (.*)$", blocks[0], re.M)}


_COQUE = re.compile(r'GROUP_MA=\("([^"]+)"\),\s*EPAIS=[^,]+,\s*VECTEUR=\(([^)]*)\)', re.M)


def comm_shell_vectors(comm: str | os.PathLike) -> dict[str, np.ndarray]:
    """``{group: VECTEUR}`` of the shells' ``AFFE_CARA_ELEM COQUE`` (:func:`..write.write_sections.write_shell_section`)."""
    comm = pathlib.Path(comm)
    if not comm.exists():
        return {}
    text = comm.read_text(encoding="utf-8", errors="replace")
    return {g: np.array([float(x) for x in v.split(",") if x.strip()]) for g, v in _COQUE.findall(text)}


def shell_frame(corners: np.ndarray, vecteur: np.ndarray) -> np.ndarray:
    """Code_Aster's local axes of a flat 3- or 4-node shell, as rows ``x, y, z`` in global axes.

    ``z`` is the normal from the node order (a quad's: of its diagonals, ``(p3 - p1) x (p4 - p2)``),
    ``x`` the reference vector ``VECTEUR`` projected onto the element's plane, ``y = z x x``.
    """
    if len(corners) == 4:
        n = np.cross(corners[2] - corners[0], corners[3] - corners[1])
    else:
        n = np.cross(corners[1] - corners[0], corners[2] - corners[0])
    n = n / np.linalg.norm(n)
    x = vecteur - np.dot(vecteur, n) * n
    norm = np.linalg.norm(x)
    if norm < 1e-6:
        raise ValueError("the shell's reference vector VECTEUR is normal to it: Code_Aster's local x is undefined")
    x = x / norm
    return np.array([x, np.cross(n, x), n])


def beam_frame(p1: np.ndarray, p2: np.ndarray, local_z: np.ndarray) -> np.ndarray:
    """A beam's local axes as rows ``x, y, z``: ``x`` along it, ``z`` the deck's GUNIVEC made normal to
    it, ``y = z x x``."""
    x = (p2 - p1) / np.linalg.norm(p2 - p1)
    z = local_z - np.dot(local_z, x) * x
    z = z / np.linalg.norm(z)
    return np.array([x, np.cross(z, x), z])


def beam_mid_forces(start: np.ndarray, end: np.ndarray, length: float) -> np.ndarray:
    """The Sesam section forces ``NXX NXY NXZ MXX MXY MXZ`` at a beam's middle from those at its ends.

    The forces and the torsion are the mean of the two ends, exact for a uniform distributed load.
    A bending moment is the mean plus what the shear's change adds: with ``V`` linear along the beam,
    ``M(L/2) = (M1 + M2) / 2 + (V1 - V2) L / 8`` up to the sign ``dM/dx`` has against ``V`` (MXY
    with NXZ, MXZ with NXY; :data:`MID_MOMENT_SIGNS`).
    """
    mid = 0.5 * (start + end)
    for m, v, sign in MID_MOMENT_SIGNS:
        mid[m] += sign * (start[v] - end[v]) * length / 8.0
    return mid


#: ``(moment index, shear index, sign)`` of :func:`beam_mid_forces`.
MID_MOMENT_SIGNS = ((4, 2, -1.0), (5, 1, 1.0))


#: GELREF1's fields: ``ELNO MATNO ADDNO INTNO MINTNO STRANO STRENO STREPONO GEONO FIXNO ECCNO TRANSNO``.
_GEONO, _FIXNO, _ECCNO, _TRANSNO = 8, 9, 10, 11


def _gelref(ref, index: int, n_nodes: int) -> int:
    """GELREF1's GEONO or TRANSNO of an element; ``-1`` there is one per node, in the record's tail (GEONO, FIXNO,
    ECCNO, TRANSNO, ``n_nodes`` each, those that are -1 only), and the first node's is taken: a flat shell's or a
    straight beam's nodes share it."""
    value = int(ref[index])
    if value != -1:
        return value
    cursor = _TRANSNO + 1
    for field in (_GEONO, _FIXNO, _ECCNO, _TRANSNO):
        if int(ref[field]) != -1:
            continue
        if field == index:
            return int(ref[cursor]) if cursor < len(ref) else -1
        cursor += n_nodes
    return -1


def _deck_maps(deck: DeckRecords):
    node_int = {int(r[0]): int(r[1]) for r in deck.get("GNODE")}
    elements = {int(r[0]): r for r in deck.get("GELMNT1")}
    gelref = {int(r[0]): r for r in deck.get("GELREF1")}
    thickness = {int(r[0]): float(r[1]) for r in deck.get("GELTH")}
    vectors = {int(r[0]): np.array(r[1:4], dtype=float) for r in deck.get("GUNIVEC")}
    coords = {int(r[0]): np.array(r[1:4], dtype=float) for r in deck.get("GCOORD")}
    return node_int, elements, gelref, thickness, vectors, coords


def code_aster_to_sin(
    rmed: str | os.PathLike,
    deck: str | os.PathLike,
    sin: str | os.PathLike | None = None,
    comm: str | os.PathLike | None = None,
) -> pathlib.Path:
    """Write the Code_Aster results ``rmed`` of the model of the Sesam deck ``deck`` as a SIN.

    ``sin``: default ``<rmed stem>R1.SIN`` beside the ``rmed``, Sestra's name for the results of
    ``<name>T1.FEM``. ``comm``: the command file (default beside ``rmed``), for the load case names and
    the shells' reference vectors.
    """
    from ada.fem.formats.sesam.results.sin_writer import SinDeck, write_sin

    rmed = pathlib.Path(rmed)
    comm = pathlib.Path(comm) if comm is not None else rmed.with_suffix(".comm")
    sin = pathlib.Path(sin) if sin is not None else rmed.with_name(f"{rmed.stem}R1.SIN")

    from ..write.write_eccentricity import load_ref_of

    records = read_deck_records(deck)
    node_int, elements, gelref, thickness, vectors, deck_coords = _deck_maps(records)
    shell_vectors = comm_shell_vectors(comm)
    # An eccentric beam is written on offset nodes the Sesam deck does not have (its GECCEN keeps the mesh nodes):
    # the sidecar beside the deck maps them back, so the MED cell is found in the deck by the nodes it shares.
    ref_of = load_ref_of(comm)

    with h5py.File(rmed, "r") as f:
        mesh = _read_mesh(f)
        n_nodes = len(mesh.coords)
        disp = _nodal_steps(f, _field_name(f, "__DEPL") or _field_name(f, "DEPL"), n_nodes)
        reac = _nodal_steps(f, _field_name(f, "__REAC_NODA") or _field_name(f, "REAC_NODA"), n_nodes)
        efge_field = _field_name(f, "__EFGE_ELNO") or _field_name(f, "EFGE_ELNO")
        efge = {t: _element_steps(f, efge_field, med) for t, med in _MED_TYPE.items()}

    cases = sorted(disp)
    if not cases:
        raise ValueError(f"{rmed} holds no displacements (DEPL): no result to convert")
    _check_same_model(mesh, node_int, deck_coords)

    names = comm_case_names(comm) or {}
    load_cases = {str(name): int(llc) for llc, name in records.text.get("TDLOAD", [])}

    out = SinDeck()
    for name, rows in records.numeric.items():
        out.add_many(name, rows)
    for name, rows in records.text.items():
        for ident, text in rows:
            out.add_text(name, ident, text)
    if "HIERARCH" not in records.numeric:
        out.add("HIERARCH", [1, 1, 1, 1, 0, 0, 0])

    # Result cases.
    for ires in cases:
        name = names.get(ires, f"case_{ires}")
        out.add("RDRESREF", [ires, 1, ires, 0, 0, 1, 10, ires, 0])
        out.add_text("TDRESREF", ires, name)
        llc = load_cases.get(name)
        if llc is not None:
            out.add("RBLODCMB", [ires, 0, 1, llc, 1.0, 0.0])

    # Displacements and reactions.
    out.add("RDNODRES", [_IRDVA, 6, 1, 2, 3, 4, 5, 6])
    nodes = sorted(node_int.items(), key=lambda kv: kv[1])
    for ires in cases:
        u = disp[ires]
        for ext, internal in nodes:
            row = u[ext - 1, :6] if ext - 1 < len(u) else np.full(6, np.nan)
            out.add("RVNODDIS", [ires, internal, _IRDVA, 0, *np.nan_to_num(row[:6])])
    supported = sorted(int(r[0]) for r in records.get("BNBCD") if any(int(round(c)) == 1 for c in r[2 : 2 + int(r[1])]))
    if reac and supported:
        out.add("RDNODREA", [_IRREA, 6, 1, 2, 3, 4, 5, 6])
        out.add("RDNODBOC", [_IRBOC, 6, 1, 1, 1, 1, 1, 1])
        ext_of = {v: k for k, v in node_int.items()}
        for ires in cases:
            r = reac.get(ires)
            if r is None:
                continue
            for internal in supported:
                out.add("RVNODREA", [ires, internal, _IRREA, _IRBOC, 0, *np.nan_to_num(r[ext_of[internal] - 1, :6])])

    # Element results.
    by_nodes = _deck_elements_by_nodes(elements, node_int)
    ext_of = {v: k for k, v in node_int.items()}
    _write_shells(out, mesh, efge, by_nodes, gelref, thickness, shell_vectors, ext_of, cases)
    _write_beams(out, mesh, efge, by_nodes, gelref, vectors, ext_of, cases, ref_of)

    write_sin(out, sin)
    logger.info(f"Wrote the Code_Aster results of {rmed.name} as a Sesam SIN: {sin}")
    return sin


def _check_same_model(mesh: _Mesh, node_int: dict[int, int], deck_coords: dict[int, np.ndarray]) -> None:
    """Refuse a deck that is not the model of the MED file: its nodes must be where the MED file has them."""
    bad = 0
    for ext, internal in node_int.items():
        p = deck_coords.get(internal)
        if p is None or ext - 1 >= len(mesh.coords) or np.linalg.norm(mesh.coords[ext - 1] - p) > 1e-4:
            bad += 1
    if bad:
        raise ValueError(
            f"{bad} of the deck's {len(node_int)} nodes are not where the MED file has them: the Sesam deck is not "
            "the model the Code_Aster results are of"
        )


def _deck_elements_by_nodes(elements: dict, node_int: dict[int, int]) -> dict[tuple, list]:
    """``{(element type, its sorted node ids): [GELMNT1 record, ...]}``, in deck order.

    The MED file and the Sesam deck number the same nodes alike but not the same elements (adapy's
    Sesam writer numbers point masses last, Code_Aster's MED writer by type), so a MED cell is found
    in the deck by its nodes.
    """
    ext_of = {v: k for k, v in node_int.items()}
    out: dict[tuple, list] = {}
    for r in elements.values():
        nodes = tuple(sorted(ext_of[int(n)] for n in r[4:] if int(n) != 0))
        out.setdefault((int(r[2]), nodes), []).append(r)
    return out


def _match(by_nodes: dict, eltyp: int, nodes) -> list | None:
    hits = by_nodes.get((eltyp, tuple(sorted(int(n) for n in nodes))))
    return hits.pop(0) if hits else None


def _deck_nodes(ge, ext_of: dict[int, int]) -> list[int]:
    """The external node ids of a GELMNT1 record, in its order."""
    return [ext_of[int(n)] for n in ge[4:] if int(n) != 0]


def _write_shells(out, mesh, efge, by_nodes, gelref, thickness, shell_vectors, ext_of, cases) -> None:
    written_types = set()
    missing, unmatched = 0, 0
    for eltyp in (FQUS, FTRS):
        comps, steps = efge[eltyp]
        ids_conn = mesh.cells.get(_MED_TYPE[eltyp])
        if ids_conn is None or not steps:
            continue
        ids, conn = ids_conn
        ci = [comps.index(c) for c in ("NXX", "NYY", "NXY", "MXX", "MYY", "MXY")]
        surface = _SURFACE_POINTS[eltyp]
        nsp = 2 * len(surface)
        for row, (med_id, nodes) in enumerate(zip(ids, conn)):
            ge = _match(by_nodes, eltyp, nodes)
            if ge is None:
                unmatched += 1
                continue
            internal = int(ge[1])
            ref = gelref.get(internal)
            t = thickness.get(_gelref(ref, _GEONO, len(nodes))) if ref is not None else None
            vec = next((shell_vectors[g] for g in mesh.groups.get(int(med_id), []) if g in shell_vectors), None)
            if t is None or vec is None:
                missing += 1
                continue
            # Code_Aster's frame is of the MED cell's node order; the result points follow the deck's.
            frame = shell_frame(mesh.coords[nodes - 1], vec)
            deck_nodes = _deck_nodes(ge, ext_of)
            at_med = [int(np.flatnonzero(nodes == n)[0]) for n in deck_nodes]
            corners = mesh.coords[np.asarray(deck_nodes) - 1]
            pts = [corners.mean(axis=0) if k == -1 else corners[k] for k in surface]
            bulk = []
            for side, sign in ((0, -1.0), (1, 1.0)):
                for j, p in enumerate(pts):
                    bulk += [side * len(surface) + j + 1, *(p + sign * 0.5 * t * frame[2])]
            out.add("RDPOINTS", [1, internal, eltyp, eltyp, nsp, _IJKDIM[eltyp], 1, 0, *bulk, *frame.reshape(-1)])
            for ires in cases:
                v = steps[ires][row][at_med][:, ci]  # (corners in deck order, 6)
                v = np.vstack([v, v.mean(axis=0)])
                at = [len(corners) if k == -1 else k for k in surface]
                n_, m_ = v[at, :3] / t, v[at, 3:] * 6.0 / t**2
                values = np.vstack([n_ - SHELL_BENDING_SIGN * m_, n_ + SHELL_BENDING_SIGN * m_])
                out.add("RVSTRESS", [ires, internal, 1, eltyp, *values.reshape(-1)])
            written_types.add(eltyp)
    for eltyp in sorted(written_types):
        out.add("RDSTRESS", [eltyp, 3, *_STRESS_CODES])
        out.add("RDIELCOR", _RDIELCOR[eltyp])
    if missing:
        logger.warning(f"{missing} shell element(s) without a thickness or a reference vector: no stresses written")
    if unmatched:
        logger.warning(f"{unmatched} shell cell(s) of the MED file are no element of the Sesam deck: not written")


def _write_beams(
    out, mesh, efge, by_nodes, gelref, vectors, ext_of, cases, ref_of: dict[int, int] | None = None
) -> None:
    """``ref_of``: offset node -> mesh node of the eccentric beams (:mod:`..write.write_eccentricity`); the deck has
    the beam on the mesh nodes, the MED file on the offset ones, and its result points are on the offset ones (the
    axis), as Sestra's are for a GECCEN beam."""
    comps, steps = efge[BEAS]
    ids_conn = mesh.cells.get(_MED_TYPE[BEAS])
    if ids_conn is None or not steps:
        return
    ids, conn = ids_conn
    order = list(BEAM_SIGNS)
    ci = [comps.index(BEAM_SIGNS[k][0]) for k in order]
    signs = np.array([BEAM_SIGNS[k][1] for k in order])
    ref_of = ref_of or {}
    any_written, reversed_, unmatched = False, 0, 0
    for row, (med_id, nodes) in enumerate(zip(ids, conn)):
        mesh_nodes = [ref_of.get(int(n), int(n)) for n in nodes]
        ge = _match(by_nodes, BEAS, mesh_nodes)
        if ge is None:
            unmatched += 1
            continue
        deck_nodes = _deck_nodes(ge, ext_of)
        if deck_nodes[0] != mesh_nodes[0]:
            reversed_ += 1
            continue
        internal = int(ge[1])
        ref = gelref.get(internal)
        z = vectors.get(_gelref(ref, _TRANSNO, len(nodes))) if ref is not None else None
        p1, p2 = mesh.coords[nodes[0] - 1], mesh.coords[nodes[1] - 1]
        length = float(np.linalg.norm(p2 - p1))
        if z is None or length == 0.0:
            continue
        frame = beam_frame(p1, p2, z)
        bulk = [1, *p1, 2, *(0.5 * (p1 + p2)), 3, *p2]
        out.add("RDPOINTS", [1, internal, BEAS, BEAS, 3, _IJKDIM[BEAS], 1, 0, *bulk, *frame.reshape(-1)])
        for ires in cases:
            v = steps[ires][row][:, ci]
            start, end = v[0] * signs, v[1] * signs
            mid = beam_mid_forces(start, end, length)
            out.add("RVFORCES", [ires, internal, 1, BEAS, *start, *mid, *end])
        any_written = True
    if any_written:
        out.add("RDFORCES", [BEAS, 6, *_FORCE_CODES])
        out.add("RDIELCOR", _RDIELCOR[BEAS])
    if reversed_:
        logger.warning(f"{reversed_} beam(s) run the other way in the Sesam deck than in the MED file: not written")
    if unmatched:
        logger.warning(f"{unmatched} beam cell(s) of the MED file are no element of the Sesam deck: not written")


__all__ = ["code_aster_to_sin", "comm_case_names", "comm_shell_vectors", "shell_frame", "beam_frame"]
