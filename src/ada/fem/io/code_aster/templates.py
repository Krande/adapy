main_comm_str = """#
#   COMM file created by ADA (Assembly for Design and Analysis)
#

# Units: N, m

DEBUT(LANG="EN", INFO=1)

mesh = LIRE_MAILLAGE(FORMAT="MED", UNITE=20)

{section_sets}

model = AFFE_MODELE(
    AFFE=(
        {model_type_str}
    ),
    MAILLAGE={input_mesh}
)

# Materials
{materials_str}

# Sections
{sections_str}

# Boundary Conditions
{bc_str}

# Step Information
{step_str}

# Results Information

FIN()
"""

el_convert_str = """{output_mesh} = CREA_MAILLAGE(
    MAILLAGE={input_mesh},
    MODI_MAILLE = _F(GROUP_MA={el_set}, OPTION='{convert_option}')
)
"""
