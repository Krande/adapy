import os
from dataclasses import dataclass


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
