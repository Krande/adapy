import pathlib

import code_aster
import libaster
from code_aster.Cata.Commands.assemblage import ASSEMBLAGE
from code_aster.Cata.Commands.calc_modes import CALC_MODES
from code_aster.Cata.Commands.impr_fonction import IMPR_FONCTION
from code_aster.Cata.Commands.impr_table import IMPR_TABLE
from code_aster.Cata.Commands.meca_statique import MECA_STATIQUE
from code_aster.Cata.Commands.proj_base import PROJ_BASE

import ada
from ada.fem.formats.code_aster.results.results_helpers import (
    export_mesh_data_to_sqlite,
)
from ada.fem.formats.code_aster.write.api_helpers import (
    assign_boundary_conditions,
    assign_element_characteristics,
    assign_element_definitions,
    assign_forces,
    assign_material_definitions,
    import_mesh,
)

USE_STAR = True
if USE_STAR:
    from code_aster.Cata.Language.SyntaxObjects import _F
    from code_aster.Commands import *

else:
    from code_aster.Commands import (
        IMPR_RESU,
        POST_ELEM,
        CREA_CHAMP,
        DEFI_LIST_REEL,
        COMB_MATR_ASSE,
        DYNA_VIBRA,
        RECU_FONCTION,
    )

    from code_aster.Supervis.ExecuteCommand import CO

from ada.fem.formats.code_aster.execute import init_close_code_aster
from ada.fem.results.sqlite_store import SQLiteFEAStore


def basic_debug(debug_dir, operator_store: dict):
    # write globals to file
    with open(f"{debug_dir}/sdof1_ca_globals_{USE_STAR}.txt", "w") as f:
        for key, value in operator_store.items():
            f.write(f"{key} = {value}\n")


@init_close_code_aster(info_level=2, temp_dir="temp")
def transient_modal_analysis(a: ada.Assembly, scratch_dir):
    if isinstance(scratch_dir, str):
        scratch_dir = pathlib.Path(scratch_dir)
        if not scratch_dir.is_absolute():
            raise ValueError("Because Code Aster")

    operator_store = globals()

    # Import Mesh
    mesh = import_mesh(a, scratch_dir=scratch_dir)

    # Assign element definitions
    model = assign_element_definitions(a, mesh)

    # Assign Materials
    material_field = assign_material_definitions(a, mesh)

    # Sections
    elem_car = assign_element_characteristics(a, model)

    # Boundary Conditions
    fix = assign_boundary_conditions(a, model)

    # Assign Forces
    forces = assign_forces(a, model)

    # Step Information
    linear_step: code_aster.ElasticResult = MECA_STATIQUE(
        MODELE=model, CHAM_MATER=material_field, CARA_ELEM=elem_car, EXCIT=(_F(CHARGE=fix), _F(CHARGE=forces))
    )

    # Results Information
    IMPR_RESU(
        MODELE=model,
        FORMAT="RESULTAT",
        RESU=_F(NOM_CHAM="DEPL", GROUP_NO="mass_set", RESULTAT=linear_step, FORMAT_R="1PE12.3"),
    )
    massin: libaster.Table = POST_ELEM(
        MODELE=model,
        CHAM_MATER=material_field,
        CARA_ELEM=elem_car,
        MASS_INER=_F(GROUP_MA=("mass_set", "spring")),
        TITRE="massin",
    )
    IMPR_TABLE(TABLE=massin, NOM_PARA=("LIEU", "MASSE"), FORMAT_R="1PE12.3")
    ASSEMBLAGE(
        MODELE=model,
        CARA_ELEM=elem_car,
        CHARGE=fix,
        NUME_DDL=CO("numdof"),
        MATR_ASSE=(_F(MATRICE=CO("rigidity"), OPTION="RIGI_MECA"), _F(MATRICE=CO("masse"), OPTION="MASS_MECA")),
    )

    rigidity = operator_store.get("rigidity")
    masse = operator_store.get("masse")
    # if nodamp:
    undamped: code_aster.ModeResult = CALC_MODES(
        TYPE_RESU="DYNAMIQUE",
        OPTION="PLUS_PETITE",
        MATR_RIGI=rigidity,
        MATR_MASS=masse,
        CALC_FREQ=_F(NMAX_FREQ=3),
    )

    IMPR_RESU(
        MODELE=model,
        FORMAT="RESULTAT",
        RESU=_F(
            RESULTAT=undamped,
            NOM_PARA=("FREQ", "MASS_GENE", "MASS_EFFE_DX", "MASS_EFFE_DY", "MASS_EFFE_DZ"),
            FORM_TABL="OUI",
        ),
    )
    IMPR_RESU(FORMAT="MED", UNITE=80, RESU=_F(RESULTAT=undamped, NOM_CHAM="DEPL"))

    numdof = operator_store.get("numdof")
    # Transient Analysis
    dsplini: libaster.FieldOnNodesReal = CREA_CHAMP(
        TYPE_CHAM="NOEU_DEPL_R",
        NUME_DDL=numdof,
        OPERATION="AFFE",
        PROL_ZERO="OUI",
        MODELE=model,
        AFFE=_F(GROUP_NO="mass_set", NOM_CMP="DX", VALE=-1),
    )
    nbvect = 3
    PROJ_BASE(
        BASE=undamped,
        NB_VECT=nbvect,
        MATR_ASSE_GENE=(_F(MATRICE=CO("stifGen"), MATR_ASSE=rigidity), _F(MATRICE=CO("massGen"), MATR_ASSE=masse)),
        VECT_ASSE_GENE=_F(VECTEUR=CO("dispGen"), TYPE_VECT="DEPL", VECT_ASSE=dsplini),
    )
    # modal transient analysis
    # #here we use the previously calculated
    # natural frequency of the system
    # to setup the stepping
    natfreq = 1.59155e-01
    # we calculate with 384 steps per period
    number = 384
    # we calculate over 4 periods
    nperiod = 4.0

    liste: libaster.ListOfFloats = DEFI_LIST_REEL(
        DEBUT=0.0, INTERVALLE=_F(JUSQU_A=nperiod / natfreq, NOMBRE=int(number * nperiod))
    )

    # make 6 calculations with varying damping factor "xi"
    it = 6
    amorG = [None] * (it + 1)
    tranG = [None] * (it + 1)
    respo = [None] * (it + 1)
    xi = [0.0, 0.01, 0.1, 0.5, 1.0, 2.0]
    # set color for the plot
    col = [1, 8, 4, 3, 11, 2]
    # [black, purple, blue, green, orange, red]
    stifGen: code_aster.GeneralizedAssemblyMatrixReal = operator_store.get("stifGen")
    massGen: code_aster.GeneralizedAssemblyMatrixReal = operator_store.get("massGen")
    dispGen: code_aster.GeneralizedAssemblyMatrixReal = operator_store.get("dispGen")

    # Define sqlite results database
    sqlite_file = (scratch_dir / a.name).with_suffix(".sqlite")
    sql_store = SQLiteFEAStore(sqlite_file, clean_tables=True)
    export_mesh_data_to_sqlite(0, a.name, mesh, sql_store)

    sql_store.insert_table(
        "FieldVars",
        [(0, "U1", "Spatial Displacement"), (1, "V1", "Spatial Velocity"), (2, "A1", "Spatial Acceleration")],
    )

    for i in range(0, it):
        sql_store.insert_table("Steps", [(i, "dynamic", f"xi={xi[i]}", "TIME")])
        amorG[i] = COMB_MATR_ASSE(CALC_AMOR_GENE=_F(RIGI_GENE=stifGen, MASS_GENE=massGen, AMOR_REDUIT=xi[i]))
        tranG[i] = DYNA_VIBRA(
            BASE_CALCUL="GENE",
            TYPE_CALCUL="TRAN",
            MATR_MASS=massGen,
            MATR_RIGI=stifGen,
            MATR_AMOR=amorG[i],
            ETAT_INIT=_F(DEPL=dispGen),
            INCREMENT=_F(LIST_INST=liste),
            SCHEMA_TEMPS=_F(SCHEMA="NEWMARK"),
        )
        respo[i] = RECU_FONCTION(
            RESU_GENE=tranG[i], TOUT_INST="OUI", NOM_CHAM="DEPL", NOM_CMP="DX", GROUP_NO="mass_set"
        )

        IMPR_FONCTION(
            FORMAT="TABLEAU", COURBE=_F(FONCTION=respo[i]), UNITE=8, TITRE="DX_endmass", SOUS_TITRE="DX_endmass"
        )

        displ: libaster.ListOfFloats = RECU_FONCTION(
            RESU_GENE=tranG[i], TOUT_INST="OUI", NOM_CHAM="DEPL", NOM_CMP="DX", GROUP_NO="mass_set"
        )
        speed: libaster.ListOfFloats = RECU_FONCTION(
            RESU_GENE=tranG[i], TOUT_INST="OUI", NOM_CHAM="VITE", NOM_CMP="DX", GROUP_NO="mass_set"
        )
        accel: libaster.ListOfFloats = RECU_FONCTION(
            RESU_GENE=tranG[i], TOUT_INST="OUI", NOM_CHAM="ACCE", NOM_CMP="DX", GROUP_NO="mass_set"
        )
        np_t, np_d, np_v, np_a = (
            liste.getValuesAsArray(),
            displ.getValuesAsArray(),
            speed.getValuesAsArray(),
            accel.getValuesAsArray(),
        )

        # Write to sqlite db HistOutput
        shared_opts = [-1, "NODAL", 0, -1, 2, i]
        sql_store.insert_table("HistOutput", [(*shared_opts, 0, x, y) for x, y in zip(np_t, np_d[:, 1])])
        sql_store.insert_table("HistOutput", [(*shared_opts, 1, x, y) for x, y in zip(np_t, np_v[:, 1])])
        sql_store.insert_table("HistOutput", [(*shared_opts, 2, x, y) for x, y in zip(np_t, np_a[:, 1])])

    undamped.printMedFile((scratch_dir / a.name).with_suffix(".rmed").as_posix())
    sql_store.conn.close()
    return sqlite_file
