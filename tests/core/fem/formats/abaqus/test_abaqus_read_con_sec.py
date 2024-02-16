from ada.fem.formats.abaqus.read.read_sections import get_connector_sections_from_bulk

CON_SEC1 = """*Connector Behavior, name=vE4
*Connector Elasticity, nonlinear, component=1, DEPENDENCIES=1
  -1.500E+04,   -1.000E-01, ,           1
   0.000E+00,    0.000E+00, ,           1
  -1.000E+03,   -1.000E-01, ,           2
   0.000E+00,    0.000E+00, ,           2
*Connector Damping, component=1, nonlinear, DEPENDENCIES=1
-11811.388300841898, -2.5        
-11731.733445044436, -2.4748743718592965
"""

CON_SEC2 = """*Connector Behavior, name=ConnProp-1_SE2
*Connector Elasticity, nonlinear, component=1, dependencies=1
1.,2., ,3.
4.,5., ,6.
*Connector Damping, nonlinear, component=1, dependencies=1, independent components=POSITION
1
1.,2.,3., ,0.
4.,5.,6., ,0.
** """


def test_con_sec1():
    connectors = get_connector_sections_from_bulk(CON_SEC1)
    con = list(connectors.values())[0]
    assert con.name == "vE4"


def test_con_sec2():
    connectors = get_connector_sections_from_bulk(CON_SEC2)
    con = list(connectors.values())[0]
    assert con.name == "ConnProp-1_SE2"
