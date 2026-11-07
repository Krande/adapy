from __future__ import annotations

from itertools import chain
from typing import TYPE_CHECKING

from ada.config import logger
from ada.materials.concept import Material

from .names import concept_name

if TYPE_CHECKING:
    from ada.api.spatial import Assembly


def materials_str(assembly: Assembly) -> str:
    all_mat = chain.from_iterable([p.materials for p in assembly.get_all_parts_in_assembly(True)])
    all_mat_unique = {x.name: x for x in all_mat}
    # A TRACTION curve only on the materials of solid sections, the ones solved with plasticity (steps.static_nonlin
    # .plastic_groups): measured, 18.1 -- a beam's stresses (SIPO_ELNO) stop at <ELEMENTS4_64> for a material with
    # any relation but ELAS, so a plastic material of a beam in a linear step failed the run.
    from ada.fem.shapes.definitions import ShellShapes

    bending = (ShellShapes.QUAD, ShellShapes.TRI, ShellShapes.QUAD8, ShellShapes.TRI6)
    solid_mats = {
        fs.material.name
        for p in assembly.get_all_parts_in_assembly(True)
        for fs in [
            *p.fem.sections.solids,
            # bending shells take plasticity too, on layers (write_sections.PLASTIC_SHELL_LAYERS), DKT and COQUE_3D
            *[
                sh
                for sh in p.fem.sections.shells
                if not sh.is_membrane and all(getattr(el, "type", None) in bending for el in sh.elset.members)
            ],
        ]
        if fs.material is not None
    }
    # and multi-fibre beams' (write_fibres): POU_D_EM integrates the curve on each fibre
    from .write_fibres import fibre_beams

    solid_mats |= {
        fs.material.name for p in assembly.get_all_parts_in_assembly(True) if p.fem is not None for fs in fibre_beams(p)
    }
    # the thermal expansion where a temperature load acts (write_temperature): ALPHA with the TEMP command variable
    from .write_steps import all_steps
    from .write_temperature import temperature_loads

    thermal = bool(temperature_loads(all_steps(assembly)))
    return "\n".join(
        [material_str(mat, plastic=mat.name in solid_mats, thermal=thermal) for mat in all_mat_unique.values()]
    )


def traction_curve(material: Material) -> list[tuple[float, float]] | None:
    """The material's hardening curve as Code_Aster's ``TRACTION`` takes it: ``(total strain, stress)`` pairs from the
    yield point on, ``eps = sig / E + eps_p``; None for a material without one.

    adapy's curve is Abaqus' ``*Plastic``: yield stress against *plastic* strain, from ``eps_p = 0``. This wrote those
    plastic strains as the total strains, and moved the first point to ``(1e-5, E 1e-5)`` -- a curve yielding at
    ``E 1e-5`` (2.1 MPa for steel), so that every plastic step would have yielded at once had the curve been used.
    """
    model = material.model
    pm = model.plasticity_model
    if pm is None or pm.eps_p is None or len(pm.eps_p) == 0:
        return None
    return [(float(s) / model.E + float(e), float(s)) for s, e in zip(pm.sig_p, pm.eps_p)]


def material_str(material: Material, plastic: bool = True, thermal: bool = False) -> str:
    """``DEFI_MATERIAU``: ``ELAS``, and a ``TRACTION`` curve (:func:`traction_curve`) for a material with plasticity
    when ``plastic`` (it is a solid's), which a step's ``RELATION='VMIS_ISOT_TRAC'`` uses (``steps.static_nonlin``)."""
    from ada.core.utils import NewLine

    model = material.model
    name = concept_name(material, "material")
    curve = traction_curve(material) if plastic else None
    nl_mat, mat_nl_in = "", ""
    if curve is not None:
        nl = NewLine(3, suffix="\t")
        values = "".join(f"{e:.10E},{s:.10E}," + next(nl) for e, s in curve)
        nl_mat = f"""{name}_cv = ({values})
{name}_tr = DEFI_FONCTION(
    NOM_PARA='EPSI', NOM_RESU='SIGM', VALE={name}_cv, INTERPOL='LIN', PROL_DROITE='LINEAIRE', PROL_GAUCHE='CONSTANT'
)"""
        mat_nl_in = f", TRACTION=_F(SIGM={name}_tr,)"
    else:
        logger.debug(f"No plasticity is defined for material {material.name}")

    alpha = f", ALPHA={float(model.alpha)!r}" if thermal and model.alpha is not None else ""
    return f"""{nl_mat}

{name} = DEFI_MATERIAU(
    ELAS=_F(E={model.E}, NU={model.v}, RHO={model.rho}{alpha}){mat_nl_in},
)
"""
