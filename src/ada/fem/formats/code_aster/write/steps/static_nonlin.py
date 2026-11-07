from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ada.fem import StepImplicitStatic
from ada.fem.loads import Load

from ..names import concept_name
from ..write_loads import write_load

if TYPE_CHECKING:
    from ada.api.spatial import Part


def large_rotation_beams(part: Part) -> bool:
    """Whether the model's beams are POU_D_T_GD, Timoshenko beams in large displacements and rotations (U3.11.05):
    when its steps are all geometrically nonlinear static ones. The element is the model's, not a step's, and
    POU_D_T_GD takes GROT_GDEP only (with ELAS_POUTRE_GR), so a model whose steps mix it with a linear, eigen,
    buckling or dynamic step keeps POU_D_E (and its nlgeom steps their small displacements, reported)."""
    from ..write_steps import all_steps

    steps = all_steps(part.get_assembly())
    beams = [fs for fs in part.fem.sections.lines if not fs.is_axial_only]
    return bool(beams) and bool(steps) and all(st.type == "static" and st.nl_geom is True for st in steps)


def _beam_groups(part: Part) -> list[str]:
    return [fs.elset.name for fs in part.fem.sections.lines if not fs.is_axial_only]


def _second_order_shell_groups(part: Part) -> list[str]:
    """The element groups of the bending shells on second-order elements (COQUE_3D)."""
    from ada.fem.shapes.definitions import ShellShapes

    second = (ShellShapes.QUAD8, ShellShapes.TRI6)
    return [
        fs.elset.name
        for fs in part.fem.sections.shells
        if not fs.is_membrane
        and fs.elset.members
        and all(getattr(el, "type", None) in second for el in fs.elset.members)
    ]


def large_displacement_groups(part: Part, report: bool = False) -> list[str]:
    """The element groups solved with large displacements and rotations in an nlgeom step: the second-order bending
    shells (COQUE_3D, ``GROT_GDEP``) and the solids (``GREEN_LAGRANGE``).

    The rest stays small-displacement, reported (``report``). U2.02.01's table gives large displacements *and large
    rotations* to COQUE_3D only; DKT takes GROT_GDEP "with low rotations" -- and measured (18.1), a DKT strip under
    GROT_GDEP whose in-plane rotation was free never converged (Newton's relative residual stuck at ~0.04, the step cut
    to 1e-12), with a negative COEF_RIGI_DRZ refused in nonlinear ("pas possible actuellement") and a larger positive
    one no better. Beams: POU_D_E has no large-rotation form. Membranes and cables have relations of their own.
    """
    from ada.fem.formats import conversion_report
    from ada.fem.shapes.definitions import ShellShapes

    from ..write_loads import STAGE

    first_order = (ShellShapes.QUAD, ShellShapes.TRI)
    sections = part.fem.sections
    shells = [fs for fs in sections.shells if not fs.is_membrane]
    dkt = [fs for fs in shells if any(getattr(el, "type", None) in first_order for el in fs.elset.members)]
    out = [fs.elset.name for fs in shells if fs not in dkt]
    out += [fs.elset.name for fs in sections.solids]
    if report:
        rep = conversion_report.current()
        if dkt:
            rep.approximated(
                STAGE,
                "FemSection",
                ", ".join(sorted(fs.name for fs in dkt)[:5]),
                "a first-order shell (DKT) in a geometrically nonlinear step is solved with small displacements: "
                "Code_Aster gives large rotations to COQUE_3D only; mesh the shells second-order for them",
                n_sections=len(dkt),
            )
        beams = [fs.name for fs in sections.lines if not fs.is_cable and not fs.is_axial_only]
        if beams and not large_rotation_beams(part):
            rep.approximated(
                STAGE,
                "FemSection",
                ", ".join(sorted(beams)[:5]),
                "a beam in a geometrically nonlinear step is solved with small displacements: its model has steps "
                "that are not all nlgeom static ones, so it keeps POU_D_E (POU_D_T_GD, the large-rotation beam, takes "
                "nothing but GROT_GDEP)",
                n_sections=len(beams),
            )
    return out


def plastic_groups(part: Part, report: bool = False) -> list[str]:
    """The element groups of the solid sections whose material has a hardening curve -- solved with von Mises
    plasticity (``VMIS_ISOT_TRAC``) in a general static step, as an Abaqus general step solves a ``*Plastic``
    material whatever its ``nlgeom``. A plastic shell or beam needs Code_Aster's layered or multi-fibre sections,
    which this writer does not make: it stays elastic, reported (``report``)."""
    from ada.fem.formats import conversion_report

    from ..write_loads import STAGE
    from ..write_materials import traction_curve

    out = []
    sections = part.fem.sections
    for fs in sections.solids:
        if traction_curve(fs.material) is not None:
            out.append(fs.elset.name)
    if report:
        for fs in [*sections.shells, *sections.lines]:
            if traction_curve(fs.material) is not None:
                conversion_report.current().approximated(
                    STAGE,
                    "FemSection",
                    fs.name,
                    "a shell or beam of a plastic material is solved elastic: Code_Aster's plasticity of shells and "
                    "beams needs layered or multi-fibre sections, which this writer does not make",
                    material=fs.material.name,
                )
    return out


@dataclass
class StatNonLin:
    name: str
    part: Part
    loads: list[Load]
    #: The step's own charge of prescribed values (it replaces the one holding them at zero); empty for none.
    prescribed: str = ""
    #: The step (for the supports in force in it); None: the model's supports.
    step: StepImplicitStatic | None = None

    @property
    def sec_str(self):
        from ..write_constraints import has_cara_elem

        sec_str = ""
        if has_cara_elem(self.part):
            sec_str = "\n    CARA_ELEM=element,"
        return sec_str

    def get_bc_str(self):
        from ada.fem.exceptions.model_definition import NoBoundaryConditionsApplied

        from ..write_constraints import step_charges

        supports = step_charges(self.part, self.step)
        charges = supports.names
        if len(charges) == 0:
            raise NoBoundaryConditionsApplied("No boundary condition is found for the specified model")
        if self.prescribed:
            charges = [c for c in charges if c != supports.zero]
        out = "".join(f"_F(CHARGE={name})," for name in charges)
        if self.prescribed:
            # ramped with the loads
            out += f"_F(CHARGE={self.prescribed}, FONC_MULT=bc_step),"
        return out

    def loads_str(self):
        # Every load of the step; only the first used to be applied. A load with an amplitude takes it as its
        # multiplier (amplitude_functions_str), the rest the step's ramp.
        out = ""
        for k, ld in enumerate(self.loads, start=1):
            mult = f"{self.name}_a{k}" if getattr(ld, "amplitude", None) is not None else "bc_step"
            out += f"_F(CHARGE={concept_name(ld, 'load')}, FONC_MULT={mult}),"
        return out

    def amplitude_functions_str(self) -> str:
        """A ``DEFI_FONCTION`` of the instant per load with an amplitude: the amplitude's (time, value) points with
        the step time mapped onto this step's instants 0 .. 1 (``timeReel``), held at its ends -- Abaqus' step-time
        amplitude, so the step's last instant is the load at its amplitude's value at the step's end."""
        total = float(self.step.total_time) if self.step is not None and self.step.total_time else 1.0
        out = []
        for k, ld in enumerate(self.loads, start=1):
            amp = getattr(ld, "amplitude", None)
            if amp is None:
                continue
            values = ", ".join(f"{float(x) / total!r}, {float(y)!r}" for x, y in zip(amp.x, amp.y))
            out.append(
                f"{self.name}_a{k} = DEFI_FONCTION(\n    NOM_PARA='INST', VALE=({values}), PROL_GAUCHE='CONSTANT', "
                f"PROL_DROITE='CONSTANT'\n)"
            )
        return "\n".join(out)

    def comportement_str(self) -> str:
        """Small strains everywhere, but for the cables (``RELATION='CABLE'``, which Code_Aster solves with large
        displacements only) and, in a geometrically nonlinear step, the membranes (``ELAS_MEMBRANE_SV`` with
        ``GROT_GDEP``: a membrane carries out-of-plane load only as it deflects)."""
        out = '_F(DEFORMATION="PETIT", TOUT="OUI"),'
        sections = self.part.fem.sections
        nl_geom = self.step is not None and self.step.nl_geom is True
        large = large_displacement_groups(self.part) if nl_geom else []
        solids = {fs.elset.name for fs in sections.solids}
        shells_large = [g for g in large if g not in solids]
        solids_large = [g for g in large if g in solids]
        if shells_large:
            # large displacements and rotations, small strains: COQUE_3D's GROT_GDEP (U2.02.01)
            out += f'_F(DEFORMATION="GROT_GDEP", GROUP_MA={_group_tuple(shells_large)}),'
        if nl_geom and large_rotation_beams(self.part):
            # POU_D_T_GD: its own relation, GROT_GDEP only (U3.11.05)
            out += f'_F(RELATION="ELAS_POUTRE_GR", DEFORMATION="GROT_GDEP", GROUP_MA={_group_tuple(_beam_groups(self.part))}),'
        if solids_large:
            # a solid takes large displacements as GREEN_LAGRANGE: GROT_GDEP on one stops at <COMPOR5_22> ("Ce n'est
            # pas possible. Vous pouvez essayer avec le modèle de déformation GREEN_LAGRANGE", measured, 18.1)
            out += f'_F(DEFORMATION="GREEN_LAGRANGE", GROUP_MA={_group_tuple(solids_large)}),'
        membranes = [fs.elset.name for fs in sections.shells if fs.is_membrane]
        if nl_geom and membranes:
            # A membrane in GROT_GDEP takes a hyperelastic relation only (<MEMBRANE_3>, measured, 18.1.7);
            # Saint Venant-Kirchhoff is the one that is linear elastic at small strain.
            out += f'_F(RELATION="ELAS_MEMBRANE_SV", DEFORMATION="GROT_GDEP", GROUP_MA={_group_tuple(membranes)}),'
        cables = [fs.elset.name for fs in sections.lines if fs.is_cable]
        if cables:
            out += f'_F(RELATION="CABLE", DEFORMATION="GROT_GDEP", GROUP_MA={_group_tuple(cables)}),'
        plastic = plastic_groups(self.part)
        if plastic:
            # von Mises plasticity with isotropic hardening along the material's TRACTION curve (U4.51.11)
            defo = "GDEF_LOG" if nl_geom else "PETIT"  # large strains with plasticity in an nlgeom step
            out += f'_F(RELATION="VMIS_ISOT_TRAC", DEFORMATION="{defo}", GROUP_MA={_group_tuple(plastic)}),'
        return out

    @property
    def max_iterations(self) -> int:
        """Newton iterations per increment: 8, or 50 with membranes or cables in the model. A flat membrane loaded
        across itself starts from no out-of-plane stiffness and Newton converges slowly out of it -- measured
        (18.1.7), a pressurised square membrane pre-stretched through its edges took 31 iterations in its first
        increment and 2 to 5 in the next ones."""
        sections = self.part.fem.sections
        if any(fs.is_membrane for fs in sections.shells) or any(fs.is_cable for fs in sections.lines):
            return 50
        if self.step is not None and self.step.nl_geom is True and large_displacement_groups(self.part):
            # large rotations: measured, 18.1, a COQUE_3D strip rolled up by an end moment took 4-7 iterations per
            # increment and, held to 8, cut its increments down to 1e-3 of the step near the end
            return 20
        return 8

    def write(self):
        return f"""{self.amplitude_functions_str()}
{self.name} = STAT_NON_LINE(
    MODELE=model,
    CHAM_MATER=material,{self.sec_str}
    COMPORTEMENT=({self.comportement_str()}),
    CONVERGENCE=_F(ARRET="OUI", ITER_GLOB_MAXI={self.max_iterations},),
    EXCIT=({self.get_bc_str()}{self.loads_str()}),
    INCREMENT=_F(LIST_INST=timeInst),
    ARCHIVAGE=_F(LIST_INST=timeReel),
)"""


def _group_tuple(names) -> str:
    return "(" + "".join(f"'{n}'," for n in names) + ")"


def _has_bending_shells(part: Part) -> bool:
    return any(not fs.is_membrane for fs in part.fem.sections.shells)


def _is_special(fs) -> bool:
    """A truss, cable or membrane section: Code_Aster computes SIEF_ELNO on them and none of the beam/shell fields."""
    return fs.is_axial_only or fs.is_membrane


def _special_groups(part: Part) -> list[str]:
    return [fs.elset.name for fs in part.fem.sections if _is_special(fs)]


@dataclass
class PostCalc:
    """The fields computed from a nonlinear result, each family asked only for what it computes.

    Beams, bending shells and solids: ``SIGM_ELNO`` (and ``EFGE`` on beams and shells) with the strains, then the
    shells' mid-layer extracted (``EXTR_COQUE``). Trusses, cables and membranes compute none of these -- measured,
    18.1.7: ``EPSI_ELGA`` on CABLE and ``EPSP_ELNO`` on MEMBTR3 stop the run at <CALCUL_37> -- so on a model holding
    them every call is restricted to the element groups it applies to (``GROUP_MA``), and those groups get
    ``SIEF_ELNO`` of their own: a cable's or truss's axial force, a membrane's in-plane forces per unit width.
    """

    stat_non_line: StatNonLin
    part: Part

    def write(self):
        name = self.stat_non_line.name
        sections = self.part.fem.sections
        special = _special_groups(self.part)
        others = [fs for fs in sections if not _is_special(fs)]
        shells = [fs.elset.name for fs in sections.shells if not fs.is_membrane]
        restrict = (lambda groups: f"\n    GROUP_MA={_group_tuple(groups)},") if special else (lambda groups: "")

        out = f"""
{name} = CALC_CHAMP(reuse={name}, RESULTAT={name}, FORCE=("REAC_NODA",))"""
        # COQUE_3D (second-order shells) computes no strains: "Le TYPE_ELEMENT MEC3QU9H ne sait pas encore calculer
        # l'option EPSP_ELGA" (<CALCUL_37>, measured, 18.1) -- their stresses and section forces only.
        # POU_D_E beams compute no strains either (<CALCUL_37>: EPSI_ELGA on MECA_POU_D_E, measured, 18.1): their
        # section forces only
        beams = [fs.elset.name for fs in sections.lines if not fs.is_axial_only and not _is_special(fs)]
        gd = large_rotation_beams(self.part)
        if beams and gd:
            # POU_D_T_GD computes no EFGE_ELNO ("Erreur de programmation", te0185, <DVP_1>, measured 18.1): its
            # displacements and reactions only
            others = [fs for fs in others if fs.elset.name not in beams]
        elif beams:
            out += f"""
{name} = CALC_CHAMP(
    reuse={name}, RESULTAT={name},
    CONTRAINTE=("EFGE_ELNO", "EFGE_NOEU"),
    GROUP_MA={_group_tuple(beams)},
)"""
            others = [fs for fs in others if fs.elset.name not in beams]
        second = _second_order_shell_groups(self.part)
        if beams and not second:
            restrict = lambda groups: f"\n    GROUP_MA={_group_tuple(groups)},"  # noqa: E731
        if second:
            out += f"""
{name} = CALC_CHAMP(
    reuse={name}, RESULTAT={name},
    CONTRAINTE=("SIGM_ELNO", "EFGE_ELNO", "EFGE_NOEU"),
    GROUP_MA={_group_tuple(second)},
)"""
            others = [fs for fs in others if fs.elset.name not in second]
            shells = [g for g in shells if g not in second]
            restrict = lambda groups: f"\n    GROUP_MA={_group_tuple(groups)},"  # noqa: E731
        if others:
            contrainte_fields = ["SIGM_ELNO"]
            if any(not fs.is_membrane for fs in sections.shells) or any(not fs.is_axial_only for fs in sections.lines):
                contrainte_fields.extend(["EFGE_ELNO", "EFGE_NOEU"])
            contrainte_str = ", ".join([f'"{field}"' for field in contrainte_fields])
            out += f"""
{name} = CALC_CHAMP(
    reuse={name}, RESULTAT={name},
    CONTRAINTE=({contrainte_str}),
    DEFORMATION=("EPSI_ELNO", "EPSP_ELNO"),{restrict([fs.elset.name for fs in others])}
)"""
        if special:
            out += f"""
{name} = CALC_CHAMP(
    reuse={name}, RESULTAT={name},
    CONTRAINTE=("SIEF_ELNO",),
    GROUP_MA={_group_tuple(special)},
)"""
        if not others:
            return out
        if len(sections.solids) > 0 or not shells:
            # The layer extraction is a shell's: a model of solids, or of beams, trusses, cables and membranes only,
            # has no layers to extract.
            return out

        groups = restrict(shells)
        return (
            out
            + f"""

stress = POST_CHAMP(
    EXTR_COQUE=_F(
        NIVE_COUCHE='MOY',
        NOM_CHAM=('SIGM_ELNO', ),
        NUME_COUCHE=1
    ),{groups}
    RESULTAT={name}
)

stress = CALC_CHAMP(
    reuse=stress,
    CONTRAINTE=('SIGM_NOEU', ),
    RESULTAT=stress
)

strain = POST_CHAMP(
    EXTR_COQUE=_F(
        NIVE_COUCHE='MOY',
    NOM_CHAM=('EPSI_ELNO', ),
    NUME_COUCHE=1),{groups}
    RESULTAT={name}
)

strainP = POST_CHAMP(
    EXTR_COQUE=_F(
        NIVE_COUCHE='MOY',
    NOM_CHAM=('EPSP_ELNO', ),
    NUME_COUCHE=1),{groups}
    RESULTAT={name}
)"""
        )


@dataclass
class ImprResu:
    stat_nl: StatNonLin
    part: Part

    def med(self, *names: str) -> str:
        """The MED field names of this step's fields: as they were (``DISP``, ...) for the first result, prefixed with
        the result's name for the next (``result2_DISP``). Every nlgeom step printed ``DISP`` and the second stopped
        Code_Aster: "<MED2_4> Le champ 'DISP' est déjà présent dans le fichier MED" (measured, 18.1.8)."""
        prefix = "" if self.stat_nl.name == "result" else f"{self.stat_nl.name}_"
        return "(" + ", ".join(f'"{prefix}{n}"' for n in names) + ",)"

    @property
    def post_calc_include(self):
        if len(self.part.fem.sections.solids) > 0 or not _has_bending_shells(self.part):
            return ""
        second = set(_second_order_shell_groups(self.part))
        if all(fs.elset.name in second for fs in self.part.fem.sections.shells if not fs.is_membrane):
            return ""  # COQUE_3D only: no layers extracted (PostCalc), so no ``stress`` to print

        return f"""_F(
            NOM_CHAM=("SIGM_ELNO", "SIGM_NOEU"),
            NOM_CHAM_MED={self.med("STRESSES_ELEM", "STRESSES_NODES")},
            RESULTAT=stress,
        ),
        _F(
            NOM_CHAM=("EPSI_ELNO",),
            NOM_CHAM_MED={self.med("STRAINS_ELEM")},
            RESULTAT=strain,
        ),
        _F(
            NOM_CHAM=("EPSP_ELNO",),
            NOM_CHAM_MED={self.med("PLASTIC_STRAINS_ELEM")},
            RESULTAT=strainP,
        ),"""

    def write(self):
        # EFGE (element forces) only makes sense for shell and beam elements, not for 3D solid elements
        from .fields import stress_fields

        # The reactions too (PostCalc computes REAC_NODA): a nonlinear step printed none, so its support forces --
        # what an equilibrium check or a closed form reads -- were not in the results at all.
        if "EFGE_ELNO" in stress_fields(self.part) and not large_rotation_beams(self.part):
            result_nom_cham = '("DEPL", "REAC_NODA", "EFGE_ELNO", "EFGE_NOEU")'
            result_nom_cham_med = self.med("DISP", "REAC_NODA", "GEN_FORCES_ELEM", "GEN_FORCES_NODES")
        else:
            result_nom_cham = '("DEPL", "REAC_NODA")'
            result_nom_cham_med = self.med("DISP", "REAC_NODA")
        special = ""
        if _special_groups(self.part):
            # The axial forces of trusses and cables and the in-plane forces per unit width of membranes.
            special = f"""_F(
            NOM_CHAM=("SIEF_ELNO",),
            NOM_CHAM_MED={self.med("SECTION_FORCES_ELEM")},
            RESULTAT={self.stat_nl.name},
            GROUP_MA={_group_tuple(_special_groups(self.part))},
        ),
        """

        return f"""IMPR_RESU(
    RESU=(
        _F(
            NOM_CHAM={result_nom_cham},
            NOM_CHAM_MED={result_nom_cham_med},
            RESULTAT={self.stat_nl.name},
        ),
        {special}{self.post_calc_include}
    ),
    UNITE=80,
)"""


def step_static_nonlin_str(step: StepImplicitStatic, part: Part, result: str = "result", applied=()) -> str:
    from ada.fem.exceptions.model_definition import NoLoadsApplied
    from ada.fem.formats.prescribed import case_values

    from ..write_bc import prescribed_charge_str
    from ..write_constraints import step_charges
    from ..write_loads import STAGE

    from ..write_temperature import is_temperature, mechanical

    every = list(applied) + [ld for ld in step.loads if all(ld is not c for c in applied)]
    thermal = any(is_temperature(ld) for ld in every)
    loads = mechanical(every)
    # the amplitudes are this step's FONC_MULT (StatNonLin.amplitude_functions_str), not left out
    load_str = "\n".join(write_load(ld, amplitude_written=True) for ld in loads)
    supports = step_charges(part, step)
    bcs = supports.bcs
    values = case_values(bcs, {step.name}, STAGE, step=step.name)
    prescribed = ""
    if any(v != 0.0 for v in values.values()):
        prescribed = f"{result}_pd"
        load_str += "\n" + prescribed_charge_str(prescribed, values, bcs)
    if len(loads) == 0 and not prescribed and not thermal:
        raise NoLoadsApplied(f"No loads are applied in step '{step}'")

    from ada.fem.formats import conversion_report

    if step.nl_geom is not True:
        conversion_report.current().note(
            STAGE,
            "STAT_NON_LINE",
            step.name,
            "a general static step of a plastic material is solved with STAT_NON_LINE, von Mises plasticity along "
            "the material's curve (VMIS_ISOT_TRAC), small strains",
        )
    # Measured (tests/fem: two nlgeom steps, a 4 m strip whose tip deflects 0.55 and 1.66 m): the linear closed form
    # to 1e-10 -- DEFORMATION='PETIT' is small displacements, and RELATION defaults to 'ELAS'.
    if step.nl_geom is True:
        large_displacement_groups(part, report=True)
    plastic_groups(part, report=True)
    stat_non_line = StatNonLin(result, part, loads, prescribed, step)
    stat_non_line_str = stat_non_line.write()
    post_calc = PostCalc(stat_non_line, part)
    post_calc_str = post_calc.write()
    iresu = ImprResu(stat_non_line, part)
    iresu_str = iresu.write()

    return f"""
{supports.definitions}
{load_str}

timeReel = DEFI_LIST_REEL(DEBUT=0.0, INTERVALLE=_F(JUSQU_A=1.0, NOMBRE=10))
timeInst = DEFI_LIST_INST(METHODE="AUTO", DEFI_LIST=_F(LIST_INST=timeReel))
bc_step = DEFI_FONCTION(NOM_PARA="INST", VALE=(0.0, 0.0, 1.0, 1.0))

{stat_non_line_str}
{post_calc_str}
{iresu_str}
"""
