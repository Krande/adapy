from __future__ import annotations

from typing import TYPE_CHECKING

from ada.fem.exceptions.element_support import IncompatibleElements
from ada.fem.formats.utils import get_fem_model_from_assembly
from ada.fem.shapes.definitions import ElemType, SolidShapes

if TYPE_CHECKING:
    from ada import Assembly, Part

#: The cells Code_Aster's sub-integrated solid modelisation, 3D_SI, accepts (U3.14.01): HEXA8 by the
#: "assumed strain" method, HEXA20 and TETRA10 by reduced Gauss integration. There is no 3D_SI for
#: TETRA4, the pentahedra, the pyramids or HEXA27.
SUB_INTEGRATED_SOLIDS = (SolidShapes.HEX8, SolidShapes.HEX20, SolidShapes.TETRA10)


def check_compatibility(assembly: Assembly):
    p = get_fem_model_from_assembly(assembly)
    step = assembly.fem.steps[0] if len(assembly.fem.steps) > 0 else None

    if p.fem.options.CODE_ASTER.use_reduced_integration:
        check_reduced_integration(p)

    if step is not None:
        has_nonlin = True if True in [x.nl_geom for x in assembly.fem.steps] else False
        for el in p.fem.elements.lines:
            if has_nonlin:
                raise IncompatibleElements(
                    "The standard Euler/Timoshenko beams in Code Aster do not support nonlinear"
                    " material assignment. todo: add option to auto-assign beams linear"
                    "material, and/or add support for fiber beams"
                )
            if el.type == ElemType.LINE_SHAPES.LINE3:
                raise IncompatibleElements("2nd order beam elements are currently not supported in Code Aster")

        for el in p.fem.elements.shell:
            if el.type in (ElemType.SHELL_SHAPES.QUAD8, ElemType.SHELL_SHAPES.TRI6) and has_nonlin:
                raise IncompatibleElements(
                    "The default 2nd order shell elements are currently not supported in "
                    "Code Aster when running the analysis using nonlinear materials"
                )


def check_reduced_integration(p: Part):
    """Reduced integration in Code_Aster is a modelisation (3D_SI), not a cell type, and only solids have one.

    The plate and shell modelisations fix their own rule -- DKQ/DSQ/Q4G integrate the stiffness 2x2
    on a quadrangle (R3.07.03), COQUE_3D integrates membrane and shear selectively (R3.07.04) -- and
    none of them, nor the beams, can be asked for less. Refused here, as the Abaqus and Calculix
    writers refuse the element types they have no reduced variant of.
    """
    if len(p.fem.elements.shell) > 0 or len(p.fem.elements.lines) > 0:
        raise IncompatibleElements(
            "Code_Aster has no reduced-integration shell or beam modelisation; "
            "reduced integration is only available for solids (3D_SI)"
        )
    for el in p.fem.elements.solids:
        if el.type not in SUB_INTEGRATED_SOLIDS:
            raise IncompatibleElements(
                f"Code_Aster's sub-integrated solid modelisation (3D_SI) does not support {el.type}; "
                f"it takes {', '.join(s.name for s in SUB_INTEGRATED_SOLIDS)}"
            )
