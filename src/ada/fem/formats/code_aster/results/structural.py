"""Code_Aster results as a format-neutral FEA result: role fields, model data, and cases read one at a time.

:func:`open_rmed_result` is the ``.rmed`` branch of ``ada.from_fem_res(path, lazy=True)``. It reads what adapy
wrote beside the run and what Code_Aster wrote:

* the input mesh ``<name>.med`` -- adapy's node and element numbers (Code_Aster keeps each cell type's
  order in the ``.rmed`` but renumbers the cells, measured on 18.1: the ``.rmed``'s ``NUM`` are its own),
  and the element groups, named back through ``<name>.name_map.json``;
* the sidecar ``<name>.adapy_fem.json`` (``..write.beams_sidecar``, schema 6): the beams' sections, local z,
  materials and member names, the shell sections' elements, thickness, material and reference vector, the
  materials' E / nu / f_y, and the restrained nodes;
* the command file ``<name>.comm``: the load-case names;
* the results ``<name>.rmed``: ``DEPL``, ``REAC_NODA`` and the element forces ``EFGE_ELNO``.

The role fields are converted to adapy's canonical (Sesam's) convention as the SIN conversion does
(``.to_sin``, measured against Sestra V11.3): shell stresses ``N/t ± 6M/t²`` at the corners and the
centroid of each surface in Code_Aster's element frame, beam forces at start, middle and end with
:data:`~.to_sin.BEAM_SIGNS`. A third-party ``.rmed`` without adapy's sidecar is refused: the ``.rmed`` holds
no sections, thicknesses or materials.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import h5py
import numpy as np

from ada.fem.formats.code_aster.common import med_cell_values
from ada.fem.formats.code_aster.results.to_sin import (
    BEAM_SIGNS,
    SHELL_BENDING_SIGN,
    beam_frame,
    beam_mid_forces,
    comm_case_names,
    shell_frame,
)
from ada.fem.results.cases import ResultCases
from ada.fem.results.field_data import FieldRole

#: Result points per surface as indices into an element's corners, -1 the centroid (``FieldRole.SHELL_STRESS``).
SURFACE_POINTS = {"QU4": (0, 1, -1, 3, 2), "TR3": (0, 1, -1, 2)}

#: ``Mesh.section_kinds`` by adapy section type.
_KIND_OF_TYPE = {
    "I": "i",
    "IG": "i",
    "TG": "i",
    "T": "i",
    "HP": "angle",
    "BG": "angle",
    "L": "angle",
    "FB": "bar",
    "BOX": "box",
    "PIPE": "pipe",
    "TUB": "pipe",
    "CIRC": "pipe",
    "UNP": "channel",
    "C": "channel",
}


def _sidecar(rmed: pathlib.Path) -> dict:
    path = rmed.with_suffix(".adapy_fem.json")
    if not path.exists():
        raise FileNotFoundError(
            f"{rmed.name} has no adapy sidecar {path.name}: a Code_Aster .rmed holds no sections, thicknesses or "
            "materials, so only a run adapy wrote (Assembly.to_fem(..., 'code_aster')) can be read as a structural "
            "result"
        )
    return json.loads(path.read_text())


def _name_map(rmed: pathlib.Path) -> dict[str, str]:
    """``{short MED group name: the model's set name}`` (``..write.name_map``)."""
    path = rmed.with_suffix(".name_map.json")
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    # The file maps short -> original, for the names that were shortened only.
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def _locate(f: h5py.File):
    name = next(iter(f["ENS_MAA"]))
    mesh = f["ENS_MAA"][name]
    if "NOE" not in mesh:
        mesh = mesh[next(iter(mesh))]
    return name, mesh


def _read_input_mesh(med: pathlib.Path, names: dict[str, str]):
    """``(coords, {MED type: (adapy ids, node ids)}, {set name: element ids})`` of adapy's input ``.med``."""
    with h5py.File(med, "r") as f:
        name, mesh = _locate(f)
        dim = int(f["ENS_MAA"][name].attrs["ESP"])
        coo = mesh["NOE"]["COO"]
        coords = np.asarray(coo[()]).reshape((int(coo.attrs["NBR"]), dim), order="F")
        if dim == 2:
            coords = np.column_stack([coords, np.zeros(len(coords))])
        family_groups: dict[int, list[str]] = {}
        fas = f["FAS"][name] if "FAS" in f and name in f["FAS"] else None
        if fas is not None and "ELEME" in fas:
            for fam in fas["ELEME"].values():
                if "GRO" in fam:
                    raw = np.asarray(fam["GRO"]["NOM"][()])
                    family_groups[int(fam.attrs["NUM"])] = [
                        "".join(chr(c) for c in row if c).strip() for row in raw.reshape(len(raw), -1)
                    ]
        cells, sets = {}, {}
        for med_type, grp in mesh["MAI"].items():
            n = int(grp["NOD"].attrs["NBR"])
            conn = np.asarray(grp["NOD"][()]).reshape((n, -1), order="F").astype(np.int64)
            ids = np.asarray(grp["NUM"][()], dtype=np.int64) if "NUM" in grp else np.arange(1, n + 1)
            cells[med_type] = (ids, conn)
            if "FAM" in grp and family_groups:
                for eid, fam in zip(ids, np.asarray(grp["FAM"][()])):
                    for g in family_groups.get(int(fam), ()):
                        sets.setdefault(names.get(g, g), []).append(int(eid))
    return coords, cells, sets


def _section(d: dict):
    from ada import Section
    from ada.sections.categories import BaseTypes

    kwargs = {k: d[k] for k in ("h", "w_top", "w_btn", "t_w", "t_ftop", "t_fbtn", "r", "wt") if d.get(k) is not None}
    return Section(name=d.get("name") or "section", sec_type=BaseTypes.from_str(d.get("type")), **kwargs)


def _material(name: str, d: dict):
    from ada import Material
    from ada.materials.metals.base_models import Metal

    # Damping and thermal expansion are no structural-check data; the sidecar carries neither.
    model = Metal(
        E=d.get("E"), rho=d.get("rho"), sig_y=d.get("sig_y"), sig_u=d.get("sig_u"), v=d.get("v"), zeta=0.0, alpha=1.2e-5
    )
    return Material(name, model)


def open_rmed_result(path: str | os.PathLike):
    """The Code_Aster results ``path`` of a run adapy wrote, as an FEA result with its cases (see the module)."""
    from ada.fem import FemSet
    from ada.fem.results.common import (
        ElementBlock,
        ElementInfo,
        FEAResult,
        FEATypes,
        FemNodes,
        Mesh,
    )
    from ada.fem.shapes.definitions import LineShapes, ShellShapes

    rmed = pathlib.Path(path)
    side = _sidecar(rmed)
    names = _name_map(rmed)
    coords, cells, sets = _read_input_mesh(rmed.with_suffix(".med"), names)

    # Materials and sections, numbered as elem_data references them.
    mat_names = sorted(side.get("materials", {}))
    mat_id = {n: i + 1 for i, n in enumerate(mat_names)}
    materials = {mat_id[n]: _material(n, side["materials"][n]) for n in mat_names}
    sections: dict[int, Any] = {}
    section_kinds: dict[int, str] = {}
    sec_key: dict[str, int] = {}
    vectors: dict[int, list] = {}
    vec_key: dict[tuple, int] = {}
    beam_of: dict[int, dict] = {}
    concept_names: dict[int, str] = {}
    elem_rows: list[list[int]] = []

    for beam in side.get("beams", []):
        sec = beam["section"]
        key = json.dumps(sec, sort_keys=True)
        if key not in sec_key:
            sid = sec_key[key] = len(sec_key) + 1
            sections[sid] = _section(sec)
            section_kinds[sid] = _KIND_OF_TYPE.get(str(sec.get("type") or "").upper(), "general")
        z = tuple(round(float(c), 12) for c in beam["local_z"])
        vid = vec_key.setdefault(z, len(vec_key) + 1)
        vectors[vid] = list(z)
        eid = int(beam["elem_id"])
        beam_of[eid] = beam
        elem_rows.append([eid, mat_id.get(beam.get("material_name"), 0), sec_key[key], vid])
        if beam.get("parent_object_name"):
            concept_names[eid] = beam["parent_object_name"]

    thickness: dict[int, float] = {}
    vecteur_of: dict[int, np.ndarray] = {}
    shell_sec = len(sec_key)
    for shell in side.get("shells", []):
        shell_sec += 1
        t = float(shell["thickness"])
        vec = np.asarray(shell["vecteur"], dtype=float) if shell.get("vecteur") else None
        for eid in shell["elem_ids"]:
            thickness[int(eid)] = t
            if vec is not None:
                vecteur_of[int(eid)] = vec
            elem_rows.append([int(eid), mat_id.get(shell.get("material_name"), 0), shell_sec, 0])
            if shell.get("concept_name"):
                concept_names[int(eid)] = shell["concept_name"]

    # Mesh blocks, frames and result points.
    shape_of = {"QU4": ShellShapes.QUAD, "TR3": ShellShapes.TRI, "SE2": LineShapes.LINE}
    blocks, frames, points = [], {}, {}
    for med_type, (ids, conn) in cells.items():
        shape = shape_of.get(med_type)
        if shape is None:
            continue
        blocks.append(ElementBlock(ElementInfo(shape, FEATypes.CODE_ASTER, med_type), node_refs=conn, identifiers=ids))
        for eid, nodes in zip(ids, conn):
            eid = int(eid)
            corners = coords[nodes - 1]
            if med_type == "SE2":
                beam = beam_of.get(eid)
                if beam is None:
                    continue
                frames[eid] = beam_frame(corners[0], corners[1], np.asarray(beam["local_z"], float))
                points[eid] = {1: corners[0], 2: 0.5 * (corners[0] + corners[1]), 3: corners[1]}
                continue
            t, vec = thickness.get(eid), vecteur_of.get(eid)
            if t is None or vec is None:
                continue
            frame = shell_frame(corners, vec)
            frames[eid] = frame
            surface = SURFACE_POINTS[med_type]
            at = [corners.mean(axis=0) if k == -1 else corners[k] for k in surface]
            pts: dict[int, np.ndarray] = {}
            for side_i, sign in ((0, -1.0), (1, 1.0)):
                for j, p in enumerate(at):
                    pts[side_i * len(surface) + j + 1] = p + sign * 0.5 * t * frame[2]
            points[eid] = pts

    mesh = Mesh(
        elements=blocks,
        nodes=FemNodes(coords=coords, identifiers=np.arange(1, len(coords) + 1)),
        sections=sections,
        materials=materials,
        vectors=vectors,
        elem_data=np.asarray(elem_rows, dtype=np.int64) if elem_rows else None,
        sets={n: FemSet(n, sorted(set(m)), set_type="elset") for n, m in sets.items()},
        eccentricities={},
    )
    mesh.thickness = thickness
    mesh.element_frames = frames
    mesh.result_points = points
    mesh.section_kinds = section_kinds
    mesh.concept_names = concept_names
    mesh.restrained_nodes = frozenset(int(n) for n in side.get("restrained_nodes", ()))

    # One result concept per static step (``result``, ``result2``, ...; write_steps.result_names), each with its
    # order numbers. One result: its order numbers are the cases, as before. Several: each (result, order number) is
    # a case of its own, numbered on, named after its step. Only ``result`` used to be read, the later steps' results
    # left out without a word.
    with h5py.File(rmed, "r") as f:
        prefixes = _result_prefixes(f)
        pairs = [
            (pre, ndt)
            for pre in prefixes
            for ndt in sorted(int(s.attrs["NDT"]) for s in f["CHA"][pre + "DEPL"].values())
        ]
    comm = rmed.with_suffix(".comm")
    if len(prefixes) <= 1:
        case_names = comm_case_names(comm) or {}
        of_case = {ndt: (pre, ndt) for pre, ndt in pairs}
        names = {ndt: case_names.get(ndt, f"case_{ndt}") for _, ndt in pairs}
    else:
        steps = comm_step_names(comm)
        of_case, names = {}, {}
        for k, (pre, ndt) in enumerate(pairs, start=1):
            of_case[k] = (pre, ndt)
            step = steps.get(pre.rstrip("_"), pre.rstrip("_"))
            several = sum(1 for p, _ in pairs if p == pre) > 1
            names[k] = f"{step} {ndt}" if several else step
    cases = ResultCases(
        stored=sorted(of_case),
        loader=_Loader(rmed, cells, thickness, frames, beam_of, of_case),
        names=names,
    )
    return FEAResult(
        rmed.stem,
        software=FEATypes.CODE_ASTER,
        results=[],
        mesh=mesh,
        results_file_path=rmed,
        step_name_map=dict(cases.names),
        cases=cases,
        analysis_kind="static",
    )


#: ``# Step <name>: <result>`` -- the comment the writer puts before each static step (write_steps.steps_str).
_STEP_COMMENT = re.compile(r"^# Step (.*): (result\d*)$", re.M)


def comm_step_names(comm: pathlib.Path) -> dict[str, str]:
    """``{result concept: step name}`` of a command file, from the writer's step comments; {} without them."""
    if not comm.exists():
        return {}
    return {
        res: name.strip() for name, res in _STEP_COMMENT.findall(comm.read_text(encoding="utf-8", errors="replace"))
    }


def _result_prefixes(f: h5py.File) -> list[str]:
    """The MED field-name prefixes of the result concepts holding a DEPL (the concept name padded to 8 characters:
    ``result__``, ``result2_``), in step order."""
    pres = [n[: -len("DEPL")] for n in f["CHA"] if n.endswith("DEPL") and len(n) == 8 + len("DEPL")]
    if not pres:
        pres = [n[: -len("DEPL")] for n in f["CHA"] if n.endswith("DEPL")]

    def order(pre: str):
        stem = pre.rstrip("_")
        digits = stem[len(stem.rstrip("0123456789")) :]
        return (int(digits) if digits else 1, pre)

    return sorted(pres, key=order)


def _field_of(f: h5py.File, prefix: str, suffix: str) -> str | None:
    name = prefix + suffix
    return name if name in f["CHA"] else None


def _field(f: h5py.File, suffix: str) -> str | None:
    names = [n for n in f["CHA"] if n == suffix or n.endswith("__" + suffix)]
    if len(names) > 1:
        raise ValueError(f"the .rmed holds {suffix} of several results ({', '.join(names)}): which one is not decided")
    return names[0] if names else None


def _step(f: h5py.File, field: str, ndt: int):
    for s in f["CHA"][field].values():
        if int(s.attrs["NDT"]) == ndt:
            return s
    raise KeyError(f"{field} has no order number {ndt}")


@dataclass
class _Loader:
    rmed: pathlib.Path
    cells: dict
    thickness: dict[int, float]
    frames: dict[int, np.ndarray]
    beam_of: dict[int, dict]
    #: ``{case: (result field prefix, order number)}``
    of_case: dict = None

    def __call__(self, steps, roles, elements, complex_phase) -> Iterator:
        from ada.fem.results.common import FEAResult, FEATypes
        from ada.fem.results.field_data import (
            ElementFieldData,
            NodalFieldData,
            NodalFieldType,
        )
        from ada.fem.shapes.definitions import LineShapes, ShellShapes

        if complex_phase:
            raise ValueError("Code_Aster results hold no complex cases")
        want = set(FieldRole) if roles is None else set(roles)
        with h5py.File(self.rmed, "r") as f:
            for case in steps:
                pre, ndt = (self.of_case or {}).get(int(case), (None, int(case)))
                if pre is None:
                    depl, reac, efge = _field(f, "DEPL"), _field(f, "REAC_NODA"), _field(f, "EFGE_ELNO")
                else:
                    depl, reac, efge = (_field_of(f, pre, s) for s in ("DEPL", "REAC_NODA", "EFGE_ELNO"))
                comps = f["CHA"][efge].attrs["NOM"].decode().split() if efge else []
                fields = []
                for role, name, ftype in (
                    (FieldRole.NODAL_DISPLACEMENT, depl, NodalFieldType.DISP),
                    (FieldRole.NODAL_REACTION, reac, NodalFieldType.FORCE),
                ):
                    if role in want and name:
                        values = _nodal(f, name, int(ndt))
                        ids = np.arange(1, len(values) + 1, dtype=float)
                        fields.append(
                            NodalFieldData(
                                role.value,
                                int(ndt),
                                ["U1", "U2", "U3", "U4", "U5", "U6"],
                                np.column_stack([ids, np.nan_to_num(values[:, :6])]),
                                field_type=ftype,
                                role=role,
                            )
                        )
                if efge and FieldRole.SHELL_STRESS in want:
                    for med_type, shape in (("QU4", ShellShapes.QUAD), ("TR3", ShellShapes.TRI)):
                        rows = self._shell_rows(f, efge, comps, med_type, int(ndt))
                        if rows is not None:
                            fields.append(
                                ElementFieldData(
                                    FieldRole.SHELL_STRESS.value,
                                    int(ndt),
                                    ["SIGXX", "SIGYY", "TAUXY"],
                                    rows,
                                    field_pos=ElementFieldData.field_pos.INT,
                                    elem_type=shape,
                                    role=FieldRole.SHELL_STRESS,
                                )
                            )
                if efge and FieldRole.BEAM_FORCES in want:
                    rows = self._beam_rows(f, efge, comps, int(ndt), elements)
                    if rows is not None:
                        fields.append(
                            ElementFieldData(
                                FieldRole.BEAM_FORCES.value,
                                int(ndt),
                                list(BEAM_SIGNS),
                                rows,
                                field_pos=ElementFieldData.field_pos.INT,
                                elem_type=LineShapes.LINE,
                                role=FieldRole.BEAM_FORCES,
                            )
                        )
                yield int(case), FEAResult(self.rmed.stem, FEATypes.CODE_ASTER, fields, None)

    def _shell_rows(self, f, efge, comps, med_type, ndt) -> np.ndarray | None:
        step = _step(f, efge, ndt)
        sup = f"NOE.{med_type}"
        if sup not in step or med_type not in self.cells:
            return None
        data = step[sup][step[sup].attrs["PFL"]]
        values = med_cell_values(data["CO"][()], int(data.attrs["NBR"]), int(data.attrs["NGA"]))
        ci = [comps.index(c) for c in ("NXX", "NYY", "NXY", "MXX", "MYY", "MXY")]
        surface = SURFACE_POINTS[med_type]
        ids, _conn = self.cells[med_type]
        out = []
        for row, eid in enumerate(ids):
            t = self.thickness.get(int(eid))
            if t is None or int(eid) not in self.frames:
                continue
            v = values[row][:, ci]
            v = np.vstack([v, v.mean(axis=0)])
            at = [len(v) - 1 if k == -1 else k for k in surface]
            n_, m_ = v[at, :3] / t, v[at, 3:] * 6.0 / t**2
            stresses = np.vstack([n_ - SHELL_BENDING_SIGN * m_, n_ + SHELL_BENDING_SIGN * m_])
            for p, s in enumerate(stresses, start=1):
                out.append([int(eid), p, *s])
        return np.asarray(out, dtype=float) if out else None

    def _beam_rows(self, f, efge, comps, ndt, elements) -> np.ndarray | None:
        step = _step(f, efge, ndt)
        if "NOE.SE2" not in step or "SE2" not in self.cells:
            return None
        data = step["NOE.SE2"][step["NOE.SE2"].attrs["PFL"]]
        values = med_cell_values(data["CO"][()], int(data.attrs["NBR"]), int(data.attrs["NGA"]))
        order = list(BEAM_SIGNS)
        ci = [comps.index(BEAM_SIGNS[k][0]) for k in order]
        signs = np.array([BEAM_SIGNS[k][1] for k in order])
        ids, conn = self.cells["SE2"]
        out = []
        for row, (eid, nodes) in enumerate(zip(ids, conn)):
            eid = int(eid)
            if eid not in self.beam_of or (elements is not None and eid not in elements):
                continue
            frame = self.frames.get(eid)
            if frame is None:
                continue
            p = np.asarray(self.beam_of[eid]["n1"], float) - np.asarray(self.beam_of[eid]["n0"], float)
            length = float(np.linalg.norm(p))
            v = values[row][:, ci]
            start, end = v[0] * signs, v[1] * signs
            mid = beam_mid_forces(start, end, length)
            for station, forces in ((1, start), (2, mid), (3, end)):
                out.append([eid, station, *forces])
        return np.asarray(out, dtype=float) if out else None


def _nodal(f: h5py.File, field: str, ndt: int) -> np.ndarray:
    step = _step(f, field, ndt)
    noe = step["NOE"]
    pfl = noe.attrs["PFL"]
    ncomp = len(f["CHA"][field].attrs["NOM"].decode().split())
    raw = np.asarray(noe[pfl]["CO"][()]).reshape((-1, ncomp), order="F")
    if pfl.decode() == "MED_NO_PROFILE_INTERNAL":
        return raw
    n_nodes = int(_locate(f)[1]["NOE"]["COO"].attrs["NBR"])
    out = np.full((n_nodes, ncomp), np.nan)
    out[np.asarray(f["PROFILS"][pfl.decode()]["PFL"][()]) - 1] = raw
    return out


__all__ = ["open_rmed_result", "SURFACE_POINTS"]
