main_comm_str = """#
#   COMM file created by ADA (Assembly for Design and Analysis)
#

# Units: N, m

DEBUT(LANG="EN", INFO=1)

mesh = LIRE_MAILLAGE(FORMAT="MED", UNITE=20)

model = AFFE_MODELE(
    AFFE=(
        {model_type_str}
    ),
    MAILLAGE=mesh
)

# Materials
{materials_str}

# Sections
{sections_str}

# Boundary Conditions
{bc_str}

# Step Information
{step_str}

FIN()
"""
