import re


def strip_comments(fbs_content: str) -> str:
    return re.sub(r"//.*", "", fbs_content)


def make_camel_case(name: str) -> str:
    """split _ separated name and return camel case"""
    return "".join([n.capitalize() for n in name.split("_")])
