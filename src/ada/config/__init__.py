import getpass
import os
import pathlib
from dataclasses import dataclass


def _get_platform_home():
    """

    :return:
    """
    # _platform_home = dict(win32="C:/ADA", linux="/home/ADA", linux2="/home/ADA", macos="/home/ADA")
    # return _platform_home[sys.platform]

    return pathlib.Path.home() / "ADA"


class Settings:
    """
    The Properties object contains all general purpose properties relevant for Parts and Assemblies

    """

    point_tol = 1e-4
    precision = 6
    mtol = 1e-3
    mmtol = 1
    valid_units = ["m", "mm"]

    convert_bad_names = False
    convert_bad_names_for_fem = True
    use_occ_bounding_box_algo = False
    use_oriented_bbox = True  # This is only relevant if OCC bounding box is True
    make_param_elbows = False
    use_param_profiles = True
    silence_display = False
    use_experimental_cache = False

    # IFC export settings
    include_ecc = True
    ifc_include_fem = False  # False while in experimental mode

    # FEM analysis settings
    if os.getenv("ADA_execute_dir", None) is not None:
        execute_dir = pathlib.Path(os.getenv("ADA_execute_dir", None))
    else:
        execute_dir = None

    # Code Aster conversion specific settings
    ca_use_meshio_med_convert = False
    ca_experimental_id_numbering = False

    # Fem Results
    return_experimental_fem_res_after_execute = False

    # Execution Settings
    use_docker_execute = False

    debug = False
    _home = _get_platform_home()
    scratch_dir = pathlib.Path(os.getenv("ADA_scratch_dir", f"{_home}/Analyses"))
    temp_dir = pathlib.Path(os.getenv("ADA_temp_dir", f"{_home}/temp"))
    debug_dir = pathlib.Path(os.getenv("ADA_log_dir", f"{_home}/logs"))
    test_dir = pathlib.Path(os.getenv("ADA_test_dir", f"{_home}/tests"))
    tools_dir = pathlib.Path(os.getenv("ADA_tools_dir", f"{_home}/tools"))

    fem_exe_paths = dict(abaqus=None, ccx=None, sesam=None, usfos=None, code_aster=None)

    @classmethod
    def default_ifc_settings(cls):
        import ifcopenshell.geom

        ifc_settings = ifcopenshell.geom.settings()
        ifc_settings.set(ifc_settings.USE_PYTHON_OPENCASCADE, True)
        ifc_settings.set(ifc_settings.SEW_SHELLS, True)
        ifc_settings.set(ifc_settings.WELD_VERTICES, True)
        ifc_settings.set(ifc_settings.INCLUDE_CURVES, True)
        ifc_settings.set(ifc_settings.USE_WORLD_COORDS, True)
        ifc_settings.set(ifc_settings.VALIDATE_QUANTITIES, True)
        return ifc_settings


@dataclass
class User:
    user_id: str = getpass.getuser()
    given_name: str = None
    family_name: str = None
    middle_names: str = None
    prefix_titles: str = None
    suffix_titles: str = None
    org_id: str = "ADA"
    org_name: str = "Assembly For Design and Analysis"
    org_description: str = None
    role: str = "Engineer"
    parent = None

    def to_ifc(self):
        from datetime import datetime

        from ada.ifc.utils import get_org, get_person

        f = self.parent.ifc_file
        actor = f.create_entity("IfcActorRole", self.role.upper(), None, None)
        user_props = dict(
            Identification=self.user_id,
            FamilyName=self.family_name,
            GivenName=self.given_name,
            MiddleNames=self.middle_names,
            PrefixTitles=self.prefix_titles,
            SuffixTitles=self.suffix_titles,
        )
        person = get_person(f, self.user_id)
        if person is None:
            person = f.create_entity("IfcPerson", **user_props, Roles=(actor,))
        organization = get_org(f, self.org_id)
        if organization is None:
            organization = f.create_entity(
                "IfcOrganization",
                Identification=self.org_id,
                Name=self.org_name,
                Description=self.org_description,
            )
        p_o = f.create_entity("IfcPersonAndOrganization", person, organization)
        application = f.create_entity("IfcApplication", organization, "XXX", "ADA", "ADA")
        timestamp = int(datetime.now().timestamp())

        return f.create_entity("IfcOwnerHistory", p_o, application, "READWRITE", None, None, None, None, timestamp)
