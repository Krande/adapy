"""What a Sesam input interface file cannot hold, named in the conversion report.

A ``T1.FEM`` file is one superelement: nodes, elements, sections, isotropic linear-elastic
materials (MISOSEL), sets, nodal masses, linear dependencies, nodal boundary conditions and the
load cases of a single analysis. Everything else an adapy model can say -- surfaces, contact,
amplitudes, predefined fields, connectors, history data, nonlinear material behaviour -- has no
card to go on, so the writer leaves it out. That is fine; leaving it out *silently* is not. Each
construct left out is recorded here once, against the adapy construct's name (``"Surface"``,
``"Amplitude"``, ...), so a conversion report says exactly what did not make it across.

The constructs that are written but not in full (a step, a load, a mass, a constraint) are
reported where they are written, by the module that writes them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from ada.fem.formats import conversion_report

if TYPE_CHECKING:
    from ada import FEM, Material
    from ada.fem import Bc

#: The ``stage`` every finding of the Sesam writer is filed under.
STAGE = "sesam writer"


def report():
    return conversion_report.current()


#: ``Bc`` types BNBCD holds. BNBCD fixes a nodal DOF (or prescribes it); a velocity or a
#: connector motion is not a DOF of the mesh at all, and writing one as a clamp -- which the
#: writer used to do -- turns a moving boundary into a fixed one.
HELD_BC_TYPES = ("displacement", "symmetry/antisymmetry/encastre", "displacement/rotation")


def _outside(members, deck_fem: FEM | None) -> bool:
    """Whether any of ``members`` belongs to a FEM other than the one the deck is written from."""
    if deck_fem is None:
        return False
    return any(getattr(m, "parent", None) not in (None, deck_fem) for m in members)


def bc_is_held(bc: Bc, deck_fem: FEM | None = None) -> bool:
    """Whether BNBCD can carry ``bc``: a displacement-type BC on a node set of the deck's mesh.

    ``deck_fem`` is the FEM whose nodes the deck writes. A BC on a node of any other FEM --
    an assembly-level reference point, say -- names a node id the deck does not have, or
    worse, one it has for a different node: BNBCD would fix that one instead.
    """
    return (
        str(bc.type).lower() in HELD_BC_TYPES
        and bc.fem_set.type == "nset"
        and not _outside(bc.fem_set.members, deck_fem)
    )


def report_bcs(fems: Iterable[FEM], deck_fem: FEM | None = None) -> None:
    """The model-level BCs BNBCD cannot hold, or holds only in part. What is not held is
    also kept from ``write_bcs.bnbcd_str`` by ``writer.to_fem``, through :func:`bc_is_held`."""
    rep = report()
    for fem in fems:
        for bc in fem.bcs:
            if str(bc.type).lower() not in HELD_BC_TYPES:
                rep.omitted(STAGE, "Bc", bc.name, f"a {bc.type} boundary condition has no Sesam form (BNBCD is nodal)")
                continue
            if bc.fem_set.type != "nset":
                rep.omitted(STAGE, "Bc", bc.name, "applied to an element set; BNBCD is nodal")
                continue
            if _outside(bc.fem_set.members, deck_fem):
                rep.omitted(STAGE, "Bc", bc.name, "on a node outside the part's mesh, which is all the deck holds")
                continue
            if any(m not in (None, 0, 0.0) for m in (bc.magnitudes or ())):
                # Written in full now (BNBCD FIX code 2 plus a BNDISPL record), so this is a
                # note, not an approximation. It stays because a settlement is loading in
                # Sesam: it lands in one load case, which is a thing to know when reading the
                # deck, and it is the one BC that does not read back as what was written --
                # the Sesam reader has no BNDISPL card, so the value is lost on the way in.
                rep.note(
                    STAGE,
                    "Bc",
                    bc.name,
                    "a prescribed displacement is written as BNBCD FIX code 2 plus a BNDISPL record "
                    "in the first load case; Sesam carries a settlement as loading",
                    magnitudes=[m for m in bc.magnitudes],
                )
            if bc.amplitude is not None:
                rep.omitted(STAGE, "Bc", bc.name, "its amplitude is not written; Sesam has no time history")


def report_materials(materials: Iterable[Material]) -> None:
    """What of each material MISOSEL leaves out.

    MISOSEL holds E, Poisson's ratio, density, the specific damping, thermal expansion and a
    yield stress. A hardening curve and Rayleigh (mass/stiffness proportional) damping are not
    material data in Sesam, and material metadata has no card at all.
    """
    rep = report()
    for mat in materials:
        model = mat.model
        pl = getattr(model, "plasticity_model", None)
        if pl is not None and pl.eps_p is not None:
            rep.omitted(
                STAGE,
                "Material",
                mat.name,
                "the plastic hardening curve has no Sesam form; MISOSEL keeps only the yield stress",
            )
        rd = getattr(model, "rayleigh_damping", None)
        if rd is not None and (rd.alpha is not None or rd.beta is not None):
            rep.omitted(
                STAGE,
                "Material",
                mat.name,
                "Rayleigh damping has no Sesam material form",
                alpha=rd.alpha,
                beta=rd.beta,
            )
        # ``aba_inp`` is the verbatim Abaqus text of a material; the typed model beside it says
        # the same, and the typed model is what MISOSEL is written from.
        meta = sorted(k for k in (mat.metadata or {}) if k != "aba_inp")
        if meta:
            rep.omitted(STAGE, "Material", mat.name, "material metadata has no Sesam form", keys=meta)


#: ``FEM`` tables with no Sesam card at all -> (the adapy construct, why).
_NOT_HELD_TABLES = (
    ("surfaces", "Surface", "a Sesam file has no surfaces; a constraint using one is written on its nodes"),
    ("intprops", "InteractionProperty", "a Sesam file has no contact"),
    ("interactions", "Interaction", "a Sesam file has no contact"),
    ("amplitudes", "Amplitude", "a Sesam file has no time histories"),
    ("predefined_fields", "PredefinedField", "a Sesam file has no initial conditions"),
    ("connector_sections", "ConnectorSection", "a Sesam file has no connector behaviour"),
)


def report_tables(fems: Iterable[FEM]) -> None:
    rep = report()
    for fem in fems:
        for attr, keyword, reason in _NOT_HELD_TABLES:
            for name in getattr(fem, attr):
                rep.omitted(STAGE, keyword, name, reason)


def report_unwritten_elements(fem: FEM) -> None:
    """The elements of a FEM whose mesh the writer does not write -- the assembly's.

    A Sesam file is the one part's mesh; ``writer.to_fem`` writes elements, masses and
    springs off the part FEM alone, so anything placed on the assembly FEM (a connector,
    typically: adapy puts those at assembly level) goes nowhere.
    """
    from ada.fem.elements import Connector, Mass
    from ada.fem.formats.sesam.write.write_elements import is_spring

    rep = report()
    for el in fem.elements:
        if isinstance(el, Connector):
            rep.omitted(STAGE, "Connector", el.name, "a connector has no Sesam element (GLSH needs a stiffness matrix)")
        elif isinstance(el, Mass):
            rep.omitted(STAGE, "Mass", el.name, "an assembly-level mass; only the part's mesh is written")
        elif is_spring(el):
            rep.omitted(STAGE, "Spring", el.name, "an assembly-level spring; only the part's mesh is written")
        else:
            rep.omitted(STAGE, "Element", str(el.id), "an assembly-level element; only the part's mesh is written")


def report_not_held(fems: Iterable[FEM], materials: Iterable[Material], written: FEM | None = None) -> None:
    """Every construct of ``fems`` the Sesam file leaves out, bar the ones reported where
    they are (partly) written: the ``written`` FEM's elements, steps, loads, masses and
    constraints."""
    fems = [f for f in fems if f is not None]
    for fem in fems:
        if fem is not written:
            report_unwritten_elements(fem)
    report_tables(fems)
    report_bcs(fems, written)
    report_materials(materials)
