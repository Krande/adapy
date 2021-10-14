from ada import Section
from ada.core.utils import roundoff


def eval_assertions(section: Section, assertions):
    props = section.properties
    for variable, should_be in assertions:
        calculated = getattr(props, variable)
        try:
            assert roundoff(calculated) == roundoff(should_be)
        except AssertionError as e:
            raise AssertionError(f"{variable}\n{e}")


def test_box():
    sec = Section("MyBGSec", from_str="BG200x200x30x30")

    assertions = [
        ("Ax", 0.0204),
        ("Ix", 0.00014739),
        ("Iy", 0.00010132),
        ("Iz", 0.00010132),
        ("Iyz", 0.0),
        ("Wxmin", 0.001734),
        ("Wymin", 0.0010132),
        ("Wzmin", 0.0010132),
        ("Shary", 0.009252968037),
        ("Sharz", 0.009252968037),
        ("Shceny", 0.0),
        ("Shcenz", 0.0),
        ("Sy", 0.000657),
        ("Sz", 0.000657),
    ]

    eval_assertions(sec, assertions)


def test_ig():
    sec = Section("MySec", from_str="IG400x200x10x20")

    assertions = [
        ("Ax", 0.0116),
        ("Ix", 1.542666667e-06),
        ("Iy", 0.0003279466667),
        ("Iz", 2.669666667e-05),
        ("Iyz", 0.0),
        ("Wxmin", 7.713333333e-05),
        ("Wymin", 0.001639733333),
        ("Wzmin", 0.0002669666667),
        ("Shary", 0.005221841891),
        # TODO: Why does this not work?
        # ("Sharz", 0.003556905278),
        # ("Sy", 0.000922),
        ("Sz", 0.0002045),
        ("Shceny", 0.0),
        ("Shcenz", 0.0),
    ]

    eval_assertions(sec, assertions)


def test_tubular():
    sec = Section("MyTUB", from_str="TUB375x35")

    assertions = [
        ("Ax", 0.07861835615608465),
        ("Ix",),
        ("Iy",),
        ("Iz",),
        ("Iyz",),
        ("Wxmin",),
        ("Wymin",),
        ("Wzmin",),
        ("Shary",),
        ("Sharz",),
        ("Shceny",),
        ("Shcenz",),
        ("Sy",),
        ("Sz",),
    ]

    eval_assertions(sec, assertions)


def test_flatbar():
    sec = Section("MyFlatbar", from_str="FB1000x1000")

    assertions = [
        ("Ax", 1.0),
        ("Ix", 0.141),
        ("Iy", 0.08333333333333333),
        ("Iz", 0.08333333333333333),
        ("Iyz", 0.0),
        ("Wxmin", 0.208),
        ("Wymin", 0.16666666666666666),
        ("Wzmin", 0.16666666666666666),
        ("Shary", 0.6666666666666666),
        ("Sharz", 0.6666666666666666),
        ("Shceny", 0.0),
        ("Shcenz", 0.0),
        ("Sy", 0.125),
        ("Sz", 0.125),
    ]

    eval_assertions(sec, assertions)


def test_angular():
    sec = Section("MySec", from_str="HP180x10")

    assertions = [
        ("Ax",),
        ("Ix",),
        ("Iy",),
        ("Iz",),
        ("Iyz",),
        ("Wxmin",),
        ("Wymin",),
        ("Wzmin",),
        ("Shary",),
        ("Sharz",),
        ("Shceny",),
        ("Shcenz",),
        ("Sy",),
        ("Sz",),
    ]

    eval_assertions(sec, assertions)


def test_channel():
    sec = Section("MySec", from_str="UNP180x10")

    assertions = [
        ("Ax",),
        ("Ix",),
        ("Iy",),
        ("Iz",),
        ("Iyz",),
        ("Wxmin",),
        ("Wymin",),
        ("Wzmin",),
        ("Shary",),
        ("Sharz",),
        ("Shceny",),
        ("Shcenz",),
        ("Sy",),
        ("Sz",),
    ]

    eval_assertions(sec, assertions)


def test_circular():
    sec = Section("MySec", from_str="CIRC180")

    assertions = [
        ("Ax",),
        ("Ix",),
        ("Iy",),
        ("Iz",),
        ("Iyz",),
        ("Wxmin",),
        ("Wymin",),
        ("Wzmin",),
        ("Shary",),
        ("Sharz",),
        ("Shceny",),
        ("Shcenz",),
        ("Sy",),
        ("Sz",),
    ]

    eval_assertions(sec, assertions)
