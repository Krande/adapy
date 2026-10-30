"""Line-element sections from the FEA results SQLite: what drawing a beam element as a solid needs.

The ODB exporter (``aba_io.py``) writes each beam section's profile as JSON (``Sections.Profile``),
which element sets carry which section (``SectionAssignments`` + ``ElementSets``) and each set's
approximate n1 direction (``MaterialOrientations``, Kind ``beam``). Here they become a
:class:`~ada.fem.results.line_sections.LineSectionTables` per instance: adapy sections, materials
and each element's local z, which both the FEAResult reader and the streaming bake use.

A database written before these tables were filled (or by an exporter that leaves them empty)
has no sections, and its beams stay lines.
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

import numpy as np

from ada.config import logger

if TYPE_CHECKING:
    from ada import Material, Section
    from ada.fem.results.line_sections import LineSectionTables

#: Abaqus' default approximate n1 for a beam in space with no orientation given.
DEFAULT_N1 = (0.0, 0.0, -1.0)


def profile_to_section(name: str, profile: dict) -> tuple["Section | None", str | None]:
    """An adapy section from an exported profile dict (see ``aba_io.profile_dict``), else
    ``(None, reason)``. The mapping is the one the input-deck reader uses for the same
    ``*Beam Section`` kinds (``read_sections.interpret_section``)."""
    from ada import Section

    kind = str(profile.get("type", "")).upper()
    p = {k: v for k, v in profile.items() if k not in ("type", "name")}
    try:
        if kind == "I":
            if not p["b1"] and not p["t1"]:
                # Abaqus has no T in its *Beam Section library; CAE (and adapy) write one as an I
                # with the bottom flange zeroed. adapy's T is the collapsed-bottom-flange form.
                return (
                    Section(
                        name, "T", h=p["h"], w_top=p["b2"], w_btn=p["t3"], t_w=p["t3"], t_ftop=p["t2"], t_fbtn=p["t2"]
                    ),
                    None,
                )
            return (
                Section(
                    name, "IG", h=p["h"], w_btn=p["b1"], w_top=p["b2"], t_w=p["t3"], t_fbtn=p["t1"], t_ftop=p["t2"]
                ),
                None,
            )
        if kind == "T":
            return (
                Section(name, "T", h=p["h"], w_top=p["b"], w_btn=p["tw"], t_w=p["tw"], t_ftop=p["tf"], t_fbtn=p["tf"]),
                None,
            )
        if kind == "BOX":
            return (
                Section(name, "BG", h=p["b"], w_btn=p["a"], w_top=p["a"], t_w=p["t1"], t_ftop=p["t2"], t_fbtn=p["t4"]),
                None,
            )
        if kind == "PIPE":
            return Section(name, "TUB", r=p["r"], wt=p["t"]), None
        if kind == "CIRC":
            return Section(name, "CIRC", r=p["r"]), None
        if kind == "RECT":
            return Section(name, "FB", h=p["b"], w_btn=p["a"], w_top=p["a"]), None
        if kind == "L":
            return (
                Section(name, "HP", h=p["b"], w_top=p["a"], w_btn=p["a"], t_w=p["t2"], t_ftop=p["t1"], t_fbtn=p["t1"]),
                None,
            )
        if kind == "ARBITRARY":
            return _channel_from_table(name, p.get("table") or [])
    except (KeyError, TypeError, ValueError) as e:
        return None, f"bad-{kind.lower()}-profile ({e})"
    return None, f"unsupported-profile[{kind.lower() or 'none'}]"


def _channel_from_table(name: str, table: list) -> tuple["Section | None", str | None]:
    """An ARBITRARY profile in the three-segment channel shape adapy writes; anything else is not
    a typed profile adapy has."""
    from ada.fem.formats.abaqus.read.read_sections import channel_from_arbitrary

    if len(table) < 2 or len(table[0]) != 2 or any(len(row) != 3 for row in table[1:]):
        return None, "unsupported-profile[arbitrary]"
    (x1, y1), (x2, y2, t1) = table[0], table[1]
    lines = [f"{len(table) - 1}, {x1}, {y1}, {x2}, {y2}, {t1}"] + [", ".join(str(v) for v in row) for row in table[2:]]
    sec = channel_from_arbitrary(name, lines, None)
    return (sec, None) if sec is not None else (None, "unsupported-profile[arbitrary]")


def _material(name: str, density, young, poisson) -> "Material":
    from ada import Material
    from ada.materials.metals import CarbonSteel

    kwargs = {}
    if density is not None:
        kwargs["rho"] = density
    if young is not None:
        kwargs["E"] = young
    if poisson is not None:
        kwargs["v"] = poisson
    return Material(name=name, mat_model=CarbonSteel(**kwargs))


def _is_line_type(el_type: str) -> bool:
    from ada.fem.formats.abaqus.elem_shapes import abaqus_el_type_to_ada
    from ada.fem.shapes.definitions import LineShapes

    try:
        return isinstance(abaqus_el_type_to_ada(el_type), LineShapes)
    except Exception:  # noqa: BLE001 - an unmapped type (connector, ...) is not a beam
        return False


def local_z_from_n1(x_vec: np.ndarray, n1) -> np.ndarray:
    """adapy's local z (the section's "up", Abaqus' n2 = t x n1) of a beam along ``x_vec`` whose
    approximate n1 is ``n1``; when n1 is along the beam, adapy's default up for the axis."""
    from ada.core.vector_utils import calc_zvec

    x = np.asarray(x_vec, dtype=float)
    z = np.cross(x, np.asarray(n1, dtype=float))
    norm = float(np.linalg.norm(z))
    if norm <= 1e-9 * max(float(np.linalg.norm(x)), 1e-300):
        z = np.asarray(calc_zvec(x), dtype=float)
        norm = float(np.linalg.norm(z))
    return z / norm


def has_line_sections(conn: sqlite3.Connection) -> bool:
    """Whether the database carries any beam profile (a cheap check before the full read)."""
    try:
        return conn.execute("SELECT 1 FROM Sections WHERE Profile IS NOT NULL LIMIT 1").fetchone() is not None
    except sqlite3.OperationalError:
        return False


def read_line_section_tables(
    conn: sqlite3.Connection, inst_id: int
) -> tuple["LineSectionTables | None", dict[str, int]]:
    """The line-element section tables of instance ``inst_id``, and per-reason counts of the line
    elements left out (no assignment, unsupported profile, ...). ``(None, {})`` when the database
    has no beam profiles at all."""
    from ada.fem.results.line_sections import LineSectionTables

    if not has_line_sections(conn):
        return None, {}

    skipped: dict[str, int] = {}

    def skip(reason: str, n: int = 1) -> None:
        skipped[reason] = skipped.get(reason, 0) + n

    materials_by_name = {
        name: (density, young, poisson)
        for name, density, young, poisson in conn.execute(
            "SELECT Name, Density, YoungsModulus, PoissonRatio FROM Materials"
        )
    }
    # Section name -> (adapy section or None, reason, material name)
    sections_by_name: dict[str, tuple] = {}
    for name, material_name, profile_json in conn.execute(
        "SELECT Name, MaterialName, Profile FROM Sections WHERE Profile IS NOT NULL"
    ):
        try:
            profile = json.loads(profile_json)
        except ValueError:
            sections_by_name[name] = (None, "bad-profile-json", material_name)
            continue
        sec, reason = profile_to_section(profile.get("name") or name, profile)
        sections_by_name[name] = (sec, reason, material_name)

    line_types = [
        t
        for (t,) in conn.execute("SELECT DISTINCT Type FROM ElementInfo WHERE InstanceID = ?", (inst_id,))
        if _is_line_type(t)
    ]
    if not line_types:
        return None, skipped
    marks = ",".join("?" * len(line_types))
    ends: dict[int, list[int]] = {}
    for el_id, point_id in conn.execute(
        f"""SELECT ec.ElemID, ec.PointID FROM ElementConnectivity ec
            JOIN ElementInfo ei ON ei.InstanceID = ec.InstanceID AND ei.ElemID = ec.ElemID
            WHERE ec.InstanceID = ? AND ei.Type IN ({marks}) ORDER BY ec.ElemID, ec.Seq""",
        (inst_id, *line_types),
    ):
        nodes = ends.setdefault(int(el_id), [int(point_id), int(point_id)])
        nodes[1] = int(point_id)
    if not ends:
        return None, skipped

    def members(set_name: str) -> list[int]:
        return [
            int(e)
            for (e,) in conn.execute(
                "SELECT ElemID FROM ElementSets WHERE InstanceID = ? AND Name = ?", (inst_id, set_name)
            )
        ]

    # Element -> section name; later assignments win, as later rows of a deck would.
    elem_section: dict[int, str] = {}
    for set_name, section_name in conn.execute(
        "SELECT SetName, SectionName FROM SectionAssignments WHERE InstanceID = ? ORDER BY rowid", (inst_id,)
    ):
        if section_name not in sections_by_name:
            continue
        for el in members(set_name):
            if el in ends:
                elem_section[el] = section_name
    elem_n1: dict[int, tuple] = {}
    for set_name, x, y, z in conn.execute(
        """SELECT SetName, XAxisX, XAxisY, XAxisZ FROM MaterialOrientations
           WHERE InstanceID = ? AND Kind = 'beam' ORDER BY rowid""",
        (inst_id,),
    ):
        if x is None or y is None or z is None:
            continue
        for el in members(set_name):
            if el in ends:
                elem_n1[el] = (float(x), float(y), float(z))

    point_ids = {pid for pair in ends.values() for pid in pair}
    coords: dict[int, np.ndarray] = {}
    for pid, x, y, z in conn.execute("SELECT ID, X, Y, Z FROM Points WHERE InstanceID = ?", (inst_id,)):
        if int(pid) in point_ids:
            coords[int(pid)] = np.array([x, y, z], dtype=float)

    sec_ids: dict[str, int] = {}
    mat_ids: dict[str, int] = {}
    sections: dict[int, "Section"] = {}
    materials: dict[int, "Material"] = {}
    vec_ids: dict[tuple, int] = {}
    rows, end_points, end_nodes = [], [], []
    for el_id in sorted(ends):
        section_name = elem_section.get(el_id)
        if section_name is None:
            skip("no-section-assignment")
            continue
        sec, reason, material_name = sections_by_name[section_name]
        if sec is None:
            skip(reason)
            continue
        first, last = ends[el_id]
        if first not in coords or last not in coords:
            skip("endpoint-not-in-mesh")
            continue
        p0, p1 = coords[first], coords[last]
        if not np.any(p1 != p0):
            skip("zero-length")
            continue
        sid = sec_ids.get(section_name)
        if sid is None:
            sid = sec_ids[section_name] = len(sec_ids) + 1
            sections[sid] = sec
        mat_key = material_name or ""
        mid = mat_ids.get(mat_key)
        if mid is None:
            mid = mat_ids[mat_key] = len(mat_ids) + 1
            materials[mid] = _material(material_name or "mat", *materials_by_name.get(material_name, (None,) * 3))
        local_z = local_z_from_n1(p1 - p0, elem_n1.get(el_id, DEFAULT_N1))
        key = tuple(round(float(v), 9) for v in local_z)
        vid = vec_ids.setdefault(key, len(vec_ids))
        rows.append((el_id, mid, sid, vid))
        end_points.append([p0, p1])
        end_nodes.append([first, last])

    if skipped:
        logger.info("results sqlite: line elements without a drawable section, by reason: %s", skipped)
    if not rows:
        return None, skipped
    return (
        LineSectionTables(
            sections=sections,
            materials=materials,
            vectors={vid: list(key) for key, vid in vec_ids.items()},
            elem_data=np.array(rows, dtype=np.int64),
            end_points=np.array(end_points, dtype=float),
            end_nodes=np.array(end_nodes, dtype=np.int64),
        ),
        skipped,
    )
