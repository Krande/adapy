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
#: fixing one makes its case fail until it is removed here.
SESAM_GAPS: dict = {
    "amplitudes": (Exception, "sections read back one per element; amplitudes dropped unreported"),
    "boundary_conditions": (
        Exception,
        "sections read back one per element; BCs read back on per-node sets; velocity/connector BCs unreported",
    ),
    "connectors": (Exception, "sections read back one per element; connectors dropped unreported"),
    "constraints": (Exception, "writer raises on a tie"),
    "constraints_assembly_level": (Exception, "reader: assembly-level node id not found"),
    "constraints_equation": (Exception, "writer raises on an equation (BLDEP holds it)"),
    "elements_line_explicit": (Exception, "writer raises on an explicit step"),
    "elements_line_profiles": (Exception, "sections read back one per element; beam profiles"),
    "elements_shell_tri7": (Exception, "writer raises on TRI7 (no Sesam element)"),
    "elements_solid_first_order": (Exception, "writer raises on PYRAMID5 (no Sesam element)"),
    "initial_conditions": (Exception, "sections read back one per element; initial conditions dropped unreported"),
    "interactions": (Exception, "sections read back one per element; surfaces and contact dropped unreported"),
    "loads": (Exception, "writer raises on a load"),
    "masses": (Exception, "reader: point-mass element id not found"),
    "masses_anisotropic": (Exception, "reader: point-mass element id not found"),
    "materials": (Exception, "sections read back one per element; plasticity/damping dropped unreported"),
    "multi_part": (Exception, "reader: element id not found after the part merge"),
    "outputs": (Exception, "sections read back one per element; steps, outputs and connectors dropped unreported"),
    "read_back_deck": (Exception, "elements change"),
    "reference_point_in_use": (
        Exception,
        "sections read back one per element; BCs on per-node sets; connectors dropped unreported",
    ),
    "sections_zero_thickness": (Exception, "reader: element id not found"),
    "sets_empty": (Exception, "reader: element id not found"),
    "springs": (Exception, "sections read back one per element; springs change"),
    "springs_coupled": (Exception, "sections read back one per element; springs change"),
    "springs_two_node": (Exception, "sections read back one per element; springs change"),
    "steps_complex_eigen": (
        Exception,
        "sections read back one per element; BCs on per-node sets; steps dropped unreported",
    ),
    "steps_dynamic_implicit": (Exception, "writer raises on an implicit dynamic step"),
    "steps_eigen": (Exception, "sections read back one per element; BCs on per-node sets; steps dropped unreported"),
    "steps_explicit": (Exception, "writer raises on an explicit step"),
    "steps_raw_input": (
        Exception,
        "sections read back one per element; BCs on per-node sets; steps dropped unreported",
    ),
    "steps_static": (Exception, "sections read back one per element; BCs on per-node sets; steps dropped unreported"),
    "steps_steady_state": (
        Exception,
        "sections read back one per element; BCs on per-node sets; steps dropped unreported",
    ),
    "surfaces": (Exception, "sections read back one per element; surfaces dropped unreported"),
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
    return {"model": model, "materials": materials, "bcs": bcs}


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
