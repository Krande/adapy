import ada
from ada import Section
from ada.core.utils import roundoff


def eval_assertions(section: Section, assertions):
    props = section.properties
    for variable, should_be in assertions:
        calculated = getattr(props, variable)
        try:
            assert roundoff(calculated, 10) == roundoff(should_be, 10)
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
        ("Ix", 1.542666667e-6),
        ("Iy", 3.279466667e-4),
        ("Iz", 2.669666667e-5),
        ("Iyz", 0.0),
        ("Wxmin", 7.713333333e-5),
        ("Wymin", 0.001639733333),
        ("Wzmin", 0.0002669666667),
        ("Shary", 0.005221841891),
        # TODO: Fix Sy calculation
        # ("Sharz", 0.003556905278),
        # ("Sy", 0.000922),
        ("Sz", 0.0002045),
        ("Shceny", 0.0),
        ("Shcenz", 0.0),
    ]

    eval_assertions(sec, assertions)


def test_tg():
    sec = Section("MySec", from_str="TG650x300x25x40")

    assertions = [
        ("Ax", 0.0116),
        ("Ix", 1.542666667e-6),
        ("Iy", 3.279466667e-4),
        ("Iz", 2.669666667e-5),
        ("Iyz", 0.0),
        ("Wxmin", 7.713333333e-5),
        ("Wymin", 0.001639733333),
        ("Wzmin", 0.0002669666667),
        ("Shary", 0.005221841891),
        # TODO: Fix Sy calculation
        # ("Sharz", 0.003556905278),
        # ("Sy", 0.000922),
        ("Sz", 0.0002045),
        ("Shceny", 0.0),
        ("Shcenz", 0.0),
    ]
    print(sec, assertions)
    # eval_assertions(sec, assertions)


def test_tubular():
    sec = Section("MyTUB", from_str="TUB375x35")

    assertions = [
        ("Ax", 0.07861835616),
        ("Ix", 0.01007199415),
        ("Iy", 0.005035997077),
        ("Iz", 0.005035997077),
        ("Iyz", 0.0),
        ("Wxmin", 0.02685865107),
        ("Wymin", 0.01342932554),
        ("Wzmin", 0.01342932554),
        ("Shary", 0.0393719232),
        ("Sharz", 0.0393719232),
        ("Shceny", 0.0),
        ("Shcenz", 0.0),
        ("Sy", 0.008953583333),
        ("Sz", 0.008953583333),
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
    # h=0.18, w=0.035, tf=0.01975, tw=0.01
    sec = Section("MySec", from_str="HP180x10")

    assertions = [
        ("Ax", 0.00229375),
        ("Ix", 1.4329e-07),
        ("Iy", 7.363586836e-06),
        ("Iz", 1.5937759e-07),
        ("Iyz", -5.432998978e-07),
        # ("Wxmin", 5.870944237e-06),
        # ("Wymin", 6.865967864e-05),
        ("Wzmin", 6.075468764e-06),
        ("Shary", 0.0004631933599),
        # ("Sharz", 0.001280395431),
        ("Shceny", -0.003767029973),
        # ("Shcenz", -0.0628773842),
        # ("Sy", 5.751025548e-05),
        ("Sz", 6.795666075e-06),
    ]

    eval_assertions(sec, assertions)


def test_channel():
    sec = Section("MySec", from_str="UNP180x10")

    assertions = [
        ("Ax", 0.002804),
        ("Ix", 9.976810667e-08),
        ("Iy", 1.364105467e-05),
        ("Iz", 1.302708818e-06),
        ("Iyz", 0.0),
        ("Wxmin", 9.069827879e-06),
        ("Wymin", 0.0001515672741),
        ("Wzmin", 2.659983343e-05),
        ("Shary", 0.001086276731),
        ("Sharz", 0.001212147612),
        ("Shceny", -0.04220927957),
        ("Shcenz", 0.0),
        ("Sy", 9.0029e-05),
        ("Sz", 2.63833268e-05),
    ]

    eval_assertions(sec, assertions)


def test_circular():
    sec = Section("MySec", from_str="CIRC100")

    assertions = [
        ("Ax", 3.141592654e-2),
        ("Ix", 1.5707963e-4),
        ("Iy", 7.853982e-5),
        ("Iz", 7.853982e-5),
        ("Iyz", 0.0),
        # ("Wxmin",),
        ("Wymin", 7.8539816e-4),
        ("Wzmin", 7.8539816e-4),
        # ("Shary",),
        # ("Sharz",),
        # ("Shceny",),
        # ("Shcenz",),
        # ("Sy",),
        # ("Sz",),
    ]

    eval_assertions(sec, assertions)

def test_tapered_section():
    sec, tap = ada.Section.from_str("TG600/300x300x10x20")
    p = ada.Part("MyBeams")
    for i in range(0, 5):
        bm = p.add_beam(ada.BeamTapered(f"bm{i}", (0, 0, i), (10, 0, i), sec, tap))
        assert isinstance(bm, ada.BeamTapered)
        assert bm.section == sec
        assert bm.taper == tap

    beams = list(p.get_all_physical_objects(by_type=ada.BeamTapered))
    assert len(beams) == 5

    beam0 = beams[0]
    assert beam0.section == sec
    assert beam0.taper == tap
