import os
import pathlib
from dataclasses import dataclass
from datetime import datetime


def _get_platform_home():
    """Home location for each platform"""
    # _platform_home = dict(win32="C:/ADA", linux="/home/ADA", linux2="/home/ADA", macos="/home/ADA")
    # return _platform_home[sys.platform]

    return pathlib.Path.home() / "ADA"


class Settings:
    """The Properties object contains all general purpose properties relevant for Parts and Assemblies"""

    point_tol = 1e-4
    precision = 6
    mtol = 1e-3
    mmtol = 1
    valid_units = ["m", "mm"]

    safe_deletion = True

    convert_bad_names = False
    convert_bad_names_for_fem = True
    use_occ_bounding_box_algo = False
    make_param_elbows = False
    use_param_profiles = True
    silence_display = False
    use_experimental_cache = False

    # IFC export settings
    include_ecc = True

    # FEM analysis settings
    if os.getenv("ADA_execute_dir", None) is not None:
        execute_dir = pathlib.Path(os.getenv("ADA_execute_dir", None))
    else:
        execute_dir = None

    # Visualization Settings
    use_new_visualize_api = False

    # Code Aster conversion specific settings
    ca_experimental_id_numbering = False

    debug = False
    _home = _get_platform_home()
    scratch_dir = pathlib.Path(os.getenv("ADA_scratch_dir", f"{_home}/Analyses"))
    temp_dir = pathlib.Path(os.getenv("ADA_temp_dir", f"{_home}/temp"))
    debug_dir = pathlib.Path(os.getenv("ADA_log_dir", f"{_home}/logs"))
    test_dir = pathlib.Path(os.getenv("ADA_test_dir", f"{_home}/tests"))
    tools_dir = pathlib.Path(os.getenv("ADA_tools_dir", f"{_home}/tools"))

    fem_exe_paths = dict(abaqus=None, ccx=None, sestra=None, usfos=None, code_aster=None)

    @classmethod
    def default_ifc_settings(cls):
        from ada.ifc.utils import default_settings

        return default_settings()


@dataclass
class User:
    user_id: str = os.environ.get("ADAUSER", "AdaUser")
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

    def _build_ifc_elem(self):
        import ifcopenshell

        from ada.ifc.read.reader_utils import get_org, get_person

        f: ifcopenshell.file = self.parent.ifc_file

        actor = None
        for ar in f.by_type("IfcActorRole"):
            if ar.Role == self.role.upper():
                actor = ar
                break

        if actor is None:
            actor = f.create_entity("IfcActorRole", Role=self.role.upper(), UserDefinedRole=None, Description=None)

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

        p_o = None
        for po in f.by_type("IfcPersonAndOrganization"):
            if po.TheOrganization != organization:
                continue
            p_o = po
            break

        if p_o is None:
            p_o = f.create_entity("IfcPersonAndOrganization", person, organization)

        app_name = "ADA"
        application = None
        for app in f.by_type("IfcApplication"):
            if app.ApplicationFullName != app_name:
                continue
            application = app
            break

        if application is None:
            application = f.create_entity("IfcApplication", organization, "XXX", "ADA", "ADA")

        timestamp = int(datetime.now().timestamp())

        owner_history = None
        for oh in f.by_type("IfcOwnerHistory"):
            if oh.OwningUser != p_o:
                continue
            if oh.OwningApplication != application:
                continue
            oh.LastModifiedDate = timestamp
            owner_history = oh
            break

        if owner_history is None:
            owner_history = f.create_entity(
                "IfcOwnerHistory",
                OwningUser=p_o,
                OwningApplication=application,
                State="READWRITE",
                ChangeAction=None,
                LastModifiedDate=None,
                LastModifyingUser=p_o,
                LastModifyingApplication=application,
                CreationDate=timestamp,
            )

        return owner_history

    def to_ifc(self):
        # Important! Needs to create unique owner_history for each use. Will cause seg fault when adding non-unique
        return self._build_ifc_elem()
