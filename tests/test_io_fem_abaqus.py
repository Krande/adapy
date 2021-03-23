import unittest
import pprint
from ada.fem.io.abaqus.common import AbaFF, AbaCards

consec = """*Node
     20,   285.025665,   130.837769,   553.482483
*Element, type=CONN3D2
9, 20, bolt1-3.477
*Connector Section, elset=Wire-1-Set-1, behavior=ConnProp-1_VISC_DAMPER_ELEM
Bushing,
"Datum csys-1",
*Element, type=CONN3D2
10, bolt1-2.477, bolt1-1.477
*Connector Section, elset=Wire-2-Set-1, behavior=ConnProp-1_VISC_DAMPER_ELEM
Bushing,
"Datum csys-2",
*Element, type=CONN3D2
11, bolt1-4.477, 19"""

conbeh = """*End Assembly
*Connector Behavior, name=ConnProp-1_VISC_DAMPER_ELEM
*Connector Elasticity, nonlinear, component=1
-350000., -0.035
-160000., -0.001
      0.,     0.
 160000.,  0.001
 350000.,  0.035
** 
** MATERIALS
** """

shell2solids = """** Constraint: co_ped2
*Coupling, constraint name=co_ped2, ref node=m_Set-7, surface=s_Set-7_CNS_
*Kinematic
** Constraint: co_ped3
*Coupling, constraint name=co_ped3, ref node=m_Set-65, surface=s_Set-65_CNS_
*Kinematic
** Constraint: sh2so1
*Shell to Solid Coupling, constraint name=sh2so1
CO_SH_inner, CO_SO_inner
** Constraint: sh2so5
*Shell to Solid Coupling, constraint name=sh2so5
m_Surf-21, s_Surf-21
** Constraint: sh2so6
*Shell to Solid Coupling, constraint name=sh2so6
m_Surf-23, s_Surf-23
*Element, type=MASS, elset=Set-69_rot_masses_MASS_
1, bolt1-1.477
2, bolt1-2.477
3, bolt1-3.477
4, bolt1-4.477
*Mass, elset=Set-69_rot_masses_MASS_
10., """

surfaces = """*Elset, elset=_s_Surf-31_S6, internal, instance=D09_partial_so-1
 5258, 5276, 5278, 5280, 5849, 5867, 5869, 5871
*Surface, type=ELEMENT, name=s_Surf-31
_s_Surf-31_S3, S3
_s_Surf-31_S5, S5
_s_Surf-31_S4, S4
_s_Surf-31_S6, S6
*Surface, type=NODE, name=s_Set-12_CNS_, internal
s_Set-12, 1.
*Surface, type=NODE, name=s_Set-14_CNS_, internal
s_Set-14, 1.
*Surface, type=NODE, name=s_Set-16_CNS_, internal
s_Set-16, 1.
*Surface, type=NODE, name=s_Set-18_CNS_, internal
s_Set-18, 1.
*Surface, type=NODE, name=s_Set-20_CNS_, internal
s_Set-20, 1.
*Surface, type=NODE, name=s_Set-22_CNS_, internal
s_Set-22, 1."""

couplings = """*Orientation, name="Datum csys-12"
0.258957691212021, 0.965888665510751,           0., -0.965888665510751, 0.258957691212021,           0.
1, 0.
** Constraint: c1
*Coupling, constraint name=c1, ref node=m_Set-12, surface=s_Set-12_CNS_
*Kinematic
1, 1
2, 2
3, 3
** Constraint: c2
*Coupling, constraint name=c2, ref node=m_Set-14, surface=s_Set-14_CNS_
*Kinematic
1, 1
2, 2
3, 3
** Constraint: c3
*Coupling, constraint name=c3, ref node=m_Set-16, surface=s_Set-16_CNS_
*Kinematic
1, 1
2, 2
3, 3"""

interactions = """** INTERACTIONS
** 
** Interaction: bump1
*Contact Pair, interaction=bumper130, type=SURFACE TO SURFACE
S02_Fused-1.s12, S02_Fused-1.s11
** Interaction: bump2
*Contact Pair, interaction=bumper110, type=SURFACE TO SURFACE
S02_Fused-1.s22, S02_Fused-1.s21
** Interaction: bump3
*Contact Pair, interaction=bumper130, small sliding, type=SURFACE TO SURFACE
S02_Fused-1.s32, S02_Fused-1.s31
**
** Interaction: Int-2
*Contact
*Contact Inclusions
m_Surf-1 , s_Surf-1
*Contact Property Assignment
 ,  , nofric
*Contact Formulation, type=MASTER SLAVE ROLES
m_Surf-1 , s_Surf-1 , SLAVE
*Contact Initialization Assignment
m_Surf-1 , s_Surf-1 , CInit-1
*Surface Property Assignment, property=GEOMETRIC CORRECTION
_Int-2_gcs0_1, Circumferential, 350.063, 99.878, 546.006,  350.989, 99.5022, 545.981
_Int-2_gcs0_2, Circumferential, 350.063, 100.122, 546.006,  350.989, 100.498, 545.981
_Int-2_gcs0_3, Circumferential, 344.598, 100., 539.316,  345.23, 100., 540.09
_Int-2_gcs0_4, Circumferential, 349.849, 100.125, 545.745,  350.319, 100.55, 544.971
_Int-2_gcs0_5, Circumferential, 349.849, 99.875, 545.745,  350.319, 99.4502, 544.971
** ----------------------------------------------------------------
** 
"""


class AbaqusIO(unittest.TestCase):
    def test_consec(self):
        a = AbaFF("Connector Section", [("elset=", "behavior="), ("contype",), ("csys",)])

        for m in a.regex.finditer(consec):
            d = m.groupdict()
            print(d)

    def test_conn_beha(self):
        conbeh_re = AbaFF(
            "Connector Behavior",
            [("name=",)],
            [("Connector Elasticity", [("nonlinear|", "component=|"), ("bulk>",)])],
        )

        print(conbeh_re.regstr)

        for m in conbeh_re.regex.finditer(conbeh):
            d = m.groupdict()
            print(d)

    def test_shell2solid(self):
        sh2so_re = AbaFF("Shell to Solid Coupling", [("constraint name=",), ("surf1", "surf2")])
        print(sh2so_re.regstr)

        for m in sh2so_re.regex.finditer(shell2solids):
            d = m.groupdict()
            print(d)

    def test_couplings(self):
        sh2so_re = AbaFF("Coupling", [("constraint name=", "ref node=", "surface=")], [("Kinematic", [(), ("bulk>",)])])
        print(sh2so_re.regstr)

        for m in sh2so_re.regex.finditer(couplings):
            d = m.groupdict()
            print(d)

    def test_surfaces(self):
        a = AbaFF("Surface", [("type=", "name=", "internal|"), ("bulk>",)])
        print(a.regstr)

        for m in a.regex.finditer(surfaces):
            d = m.groupdict()
            print(d)

    def test_contact_pairs(self):
        for m in AbaCards.contact_pairs.regex.finditer(interactions):
            d = m.groupdict()
            print(d)

    def test_contact_general(self):
        for m in AbaCards.contact_general.regex.finditer(interactions):
            d = m.groupdict()
            pprint.pprint(d, indent=4)


if __name__ == "__main__":
    unittest.main()
