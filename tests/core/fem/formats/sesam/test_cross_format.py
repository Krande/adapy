"""Abaqus -> Sesam -> Abaqus: what Sesam can hold comes through exactly, and what it cannot is
reported -- never lost silently.

Sesam's input interface file is one superelement: a mesh with its sections, materials, sets,
point masses, springs, linear dependencies and boundary conditions. It has no parts, surfaces,
contact, amplitudes, connectors or history data. So the comparison is of :func:`sesam_view`,
the part of the whole-model canonical form (``abaqus/canonical.py``) a Sesam file can express,
and every table outside it that the model uses must be named in the conversion report the
Sesam writer fills in. The way back needs no such allowance: Abaqus holds everything a Sesam
file does, so Sesam -> Abaqus reports nothing omitted and changes nothing.

The zoo is the Abaqus one, read from a written deck first, so the Sesam writer sees what a
real Abaqus conversion hands it.

On top of the canonical form's rules (R1-R20), the view has three of its own:

S1  Numbers are compared to 9 significant digits. A Sesam file's data fields are FORTRAN
    ``E16.8`` (manual 2.1 and 9, "four 16character data fields (4E16.8)"): 9 significant
    digits is all a value can carry, where the canonical form compares 12. That is the format,
    not a defect; the writer records it as a note with the largest relative change.
S2  ``ENCASTRE`` is compared as the displacement BC fixing all six DOFs, which is what it is:
    Abaqus's name for them. BNBCD holds fixed DOFs, not the name. (The Abaqus reader already
    reads the other named restraints, PINNED, XSYMM, ..., as the DOFs they fix.)
S3  A node an equation term names is compared by its id alone, not as ``[part, id]`` (R11):
    the parts are merged into the one superelement, which has no part to name.
"""

from __future__ import annotations

import pathlib

import pytest

import ada
from ada.fem.formats import conversion_report

from ..abaqus.canonical import canonical, diff_paths
from ..abaqus.zoo import ZOO

#: What a Sesam file holds, per FEM. Parts are merged: Sesam has one superelement.
SESAM_TABLES = ("nodes", "elements", "sets", "sections", "masses", "springs", "constraints")

#: Tables of the canonical form Sesam cannot hold -> the adapy construct the Sesam writer names
#: (as the finding's ``keyword``) when it leaves one out.
NOT_HELD = {
    "surfaces": "Surface",
    "interaction_properties": "InteractionProperty",
    "interactions": "Interaction",
    "amplitudes": "Amplitude",
    "predefined_fields": "PredefinedField",
    "steps": "Step",
    "nonstructural_masses": "Mass",
    "connectors": "Connector",
    "connector_sections": "ConnectorSection",
}

SESAM_WRITER = "sesam writer"

#: Models whose Sesam conversion still loses something unreported, or changes it. Strict:
#: fixing one makes its case fail until it is removed here. "reader:" is what the Sesam reader
#: changes on the way back; "writer:" is a loss the writer reports but that still changes the
#: compared view, because Sesam has no form for it (or the writer none yet).
SESAM_GAPS: dict = {
    "boundary_conditions": (
        Exception,
        "reader: a prescribed displacement is now written in full (BNBCD FIX code 2 plus a BNDISPL "
        "record) but the Sesam reader has no BNDISPL card, so its magnitude does not come back; "
        "writer: velocity and connector BCs have no BNBCD form (all reported)",
    ),
    "constraints": (
        Exception,
        "writer: tie and MPC have no BLDEP form, a shell-to-solid coupling is written as rigid links, a "
        "coupling's rotations and orientation are not written, and the rigid body shares its links with "
        "a coupling on the same reference node, which BLDEP merges (all reported); a coupling on a "
        "surface reads back on the surface's node set",
    ),
    "elements_line_profiles": (
        Exception,
        "writer: a solid round bar is written as a GPIPE with a 1% bore (the manual does not say a zero "
        "inner diameter is valid), and reads back as a tube (reported, with the area change)",
    ),
    "elements_shell_tri7": (Exception, "writer: TRI7 has no Sesam element type (reported)"),
    "elements_solid_first_order": (Exception, "writer: PYRAMID5 has no Sesam element type (reported)"),
    "multi_part": (
        Exception,
        "by design: both parts number nodes and elements from 1, and one superelement cannot hold both "
        "under those ids; the merge renumbers the second part (reported)",
    ),
    "reference_point_in_use": (
        Exception,
        "writer: the BC on the assembly-level reference point has no node in the deck (reported)",
    ),
}


def sesam_view(c: dict) -> dict:
    model = {t: {} for t in SESAM_TABLES}
    for part in c["parts"].values():
        for t in SESAM_TABLES:
            model[t].update(part[t])
    # The shape, not the Abaqus type: which formulation a Sesam element stands for is the
    # formulation mapping's business, compared by its own tests.
    model["elements"] = {k: {f: v for f, v in e.items() if f != "type"} for k, e in model["elements"].items()}
    model["constraints"] = {k: _terms_by_node_id(con) for k, con in model["constraints"].items()}
    bcs = {k.split(".", 1)[-1]: _restraint_as_dofs(v) for k, v in c["bcs"].items()}
    materials = {k: {f: m[f] for f in MATERIAL_FIELDS} for k, m in c["materials"].items()}
    return _e16_8({"model": model, "materials": materials, "bcs": bcs})


def _terms_by_node_id(con: dict) -> dict:
    """S3."""
    terms = con.get("equation_terms")
    if not terms:
        return con
    return {**con, "equation_terms": [[r[1] if isinstance(r, list) else r, dof, c] for r, dof, c in terms]}


def _restraint_as_dofs(bc: dict) -> dict:
    """S2."""
    if bc["dofs"] == "encastre" or bc["dofs"] == {"encastre": None}:
        return {**bc, "type": "displacement", "dofs": {str(d): None for d in range(1, 7)}}
    return bc


def _e16_8(value):
    """S1: every float as a Sesam field holds it, nine significant digits."""
    if isinstance(value, float):
        return float(f"{value:.8E}")
    if isinstance(value, dict):
        return {k: _e16_8(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_e16_8(v) for v in value]
    return value


#: What MISOSEL holds of a material: the linear elastic isotropic constants.
MATERIAL_FIELDS = ("E", "v", "rho", "expansion", "specific_damping")


def _material_beyond_misosel(m: dict) -> bool:
    return any(x is not None for x in m["damping"]) or m["plastic"] is not None or bool(m["metadata"])


def not_held(c: dict) -> set[str]:
    """The constructs of ``c`` that a Sesam file cannot hold."""
    used = set()
    for part in c["parts"].values():
        used |= {NOT_HELD[t] for t in NOT_HELD if part.get(t)}
    used |= {NOT_HELD[t] for t in ("connectors", "connector_sections") if c.get(t)}
    if any(_material_beyond_misosel(m) for m in c["materials"].values()):
        used.add("Material")
    return used


def _abaqus_round(model: ada.Assembly, d: pathlib.Path, tag: str) -> ada.Assembly:
    model.to_fem(tag, fem_format="abaqus", scratch_dir=d, overwrite=True, write_input_files_only=True)
    return ada.from_fem(next(d.rglob(f"{tag}.inp")), "abaqus")


def _cases():
    for name in sorted(ZOO):
        gap = SESAM_GAPS.get(name)
        marks = [pytest.mark.xfail(raises=gap[0], strict=True, reason=gap[1])] if gap else []
        yield pytest.param(name, marks=marks, id=name)


@pytest.mark.parametrize("name", _cases())
def test_abaqus_to_sesam_and_back(name, tmp_path):
    source = _abaqus_round(ZOO[name](), tmp_path / "abaqus", "src")
    original = canonical(source)

    with conversion_report.collect() as to_sesam:
        source.to_fem("s", fem_format="sesam", scratch_dir=tmp_path / "sesam", overwrite=True)
    via_sesam = ada.from_fem(next((tmp_path / "sesam").rglob("sT1.FEM")), "sesam")

    diffs = diff_paths(sesam_view(original), sesam_view(canonical(via_sesam)))
    assert not diffs, "Abaqus -> Sesam changed:\n  " + "\n  ".join(diffs[:80])

    reported = {f.keyword for f in to_sesam.findings if f.stage == SESAM_WRITER}
    unreported = not_held(original) - reported
    assert not unreported, f"left out of the Sesam file without a word: {sorted(unreported)}"

    with conversion_report.collect() as to_abaqus:
        back = _abaqus_round(via_sesam, tmp_path / "back", "back")
    lost = [f for f in to_abaqus.findings if f.kind in ("omitted", "approximated")]
    assert not lost, f"Sesam -> Abaqus lost: {[(f.keyword, f.subject, f.reason) for f in lost]}"
    diffs = diff_paths(sesam_view(canonical(via_sesam)), sesam_view(canonical(back)))
    assert not diffs, "Sesam -> Abaqus changed:\n  " + "\n  ".join(diffs[:80])
