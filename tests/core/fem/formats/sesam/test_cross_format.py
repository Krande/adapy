"""Abaqus -> Sesam -> Abaqus: what Sesam can hold comes through exactly, and what it cannot is
reported -- never lost silently.

Sesam's input interface file is one superelement: a mesh with its sections, materials, sets,
point masses, springs, linear dependencies and boundary conditions. It has no parts, surfaces,
contact, amplitudes, connectors or history data. So the comparison is of :func:`sesam_view`,
the part of the whole-model canonical form (``abaqus/canonical.py``) a Sesam file can express,
and every table outside it that the model uses must be named in the conversion report the
Sesam writer fills in. The way back needs no such allowance: Abaqus holds everything a Sesam
file does, so Sesam -> Abaqus reports nothing omitted and changes nothing.

Numbers are compared at the nine significant digits a Sesam field holds (E16.8): that is the
format, not a defect, and the writer records it as a note with the largest relative change.

The zoo is the Abaqus one, read from a written deck first, so the Sesam writer sees what a
real Abaqus conversion hands it.
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
    "amplitudes": (Exception, "reader: sections read back one per element"),
    "boundary_conditions": (
        Exception,
        "reader: sections read back one per element; reader: BCs read back on per-node sets; "
        "writer: velocity and connector BCs have no BNBCD form (reported)",
    ),
    "connectors": (Exception, "reader: sections read back one per element"),
    "constraints": (
        Exception,
        "reader: sections read back one per element; reader: couplings come back named per master node; "
        "writer: tie and MPC have no BLDEP form (reported)",
    ),
    "constraints_assembly_level": (
        Exception,
        "reader: sections read back one per element; reader: the coupling comes back named per master node",
    ),
    "constraints_equation": (
        Exception,
        "reader: sections read back one per element; "
        "reader: equations come back named per dependent dof, with node terms for set terms",
    ),
    "elements_line_explicit": (Exception, "reader: sections read back one per element"),
    "elements_line_profiles": (
        Exception,
        "reader: sections read back one per element; beam profiles; the way back loses a general beam section",
    ),
    "elements_line_second_order": (Exception, "reader raises on a second-order beam section"),
    "elements_line_verbatim": (Exception, "reader: sections read back one per element"),
    "elements_shell": (Exception, "reader: sections read back one per element"),
    "elements_shell_second_order": (Exception, "reader: sections read back one per element"),
    "elements_shell_tri6": (Exception, "reader: sections read back one per element"),
    "elements_shell_tri7": (
        Exception,
        "writer: TRI7 has no Sesam element type (reported); GCOORD's 4E16.8 keeps nine significant digits",
    ),
    "elements_solid_first_order": (
        Exception,
        "reader: sections read back one per element; writer: PYRAMID5 has no Sesam element type (reported)",
    ),
    "elements_solid_second_order": (Exception, "reader: sections read back one per element"),
    "elements_solid_wedge": (Exception, "reader: sections read back one per element"),
    "initial_conditions": (Exception, "reader: sections read back one per element"),
    "interactions": (Exception, "reader: sections read back one per element"),
    "loads": (Exception, "reader: sections read back one per element"),
    "masses": (Exception, "reader: point-mass element id not found"),
    "masses_anisotropic": (Exception, "reader: point-mass element id not found"),
    "materials": (Exception, "reader: sections read back one per element"),
    "multi_part": (Exception, "reader: point-mass element id not found"),
    "outputs": (Exception, "reader: sections read back one per element"),
    "read_back_deck": (Exception, "writer: the deck has no sections, and GELREF1 needs one (reported)"),
    "reference_point": (Exception, "reader: sections read back one per element"),
    "reference_point_in_use": (
        Exception,
        "reader: sections read back one per element; "
        "writer: the BC on the assembly-level reference point has no node in the deck (reported)",
    ),
    "sections_zero_thickness": (Exception, "writer: the elements come without a section, GELREF1 needs one (reported)"),
    "sets": (Exception, "reader: sections read back one per element"),
    "sets_empty": (Exception, "reader: element id not found"),
    "springs": (
        Exception,
        "reader: sections read back one per element; writer: no GELMNT1 + MGSPRNG for springs yet (reported)",
    ),
    "springs_coupled": (
        Exception,
        "reader: sections read back one per element; writer: no GELMNT1 + MGSPRNG for springs yet (reported)",
    ),
    "springs_two_node": (
        Exception,
        "reader: sections read back one per element; writer: no GELMNT1 + MGSPRNG for springs yet (reported)",
    ),
    "steps_complex_eigen": (
        Exception,
        "reader: sections read back one per element; reader: BCs read back on per-node sets",
    ),
    "steps_dynamic_implicit": (
        Exception,
        "reader: sections read back one per element; reader: BCs read back on per-node sets",
    ),
    "steps_eigen": (Exception, "reader: sections read back one per element; reader: BCs read back on per-node sets"),
    "steps_explicit": (Exception, "reader: sections read back one per element"),
    "steps_raw_input": (
        Exception,
        "reader: sections read back one per element; reader: BCs read back on per-node sets",
    ),
    "steps_static": (Exception, "reader: sections read back one per element; reader: BCs read back on per-node sets"),
    "steps_steady_state": (
        Exception,
        "reader: sections read back one per element; reader: BCs read back on per-node sets",
    ),
    "surfaces": (Exception, "reader: sections read back one per element"),
}


def sesam_view(c: dict) -> dict:
    model = {t: {} for t in SESAM_TABLES}
    for part in c["parts"].values():
        for t in SESAM_TABLES:
            model[t].update(part[t])
    # The shape, not the Abaqus type: which formulation a Sesam element stands for is the
    # formulation mapping's business, compared by its own tests.
    model["elements"] = {k: {f: v for f, v in e.items() if f != "type"} for k, e in model["elements"].items()}
    bcs = {k.split(".", 1)[-1]: v for k, v in c["bcs"].items()}
    materials = {k: {f: m[f] for f in MATERIAL_FIELDS} for k, m in c["materials"].items()}
    return _e16_8({"model": model, "materials": materials, "bcs": bcs})


def _e16_8(value):
    """Every float as a Sesam field holds it: nine significant digits."""
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
