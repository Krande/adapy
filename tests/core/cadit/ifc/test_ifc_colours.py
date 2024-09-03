from ada import Assembly, Beam, Part, User
from ada.visit.colors import color_dict


def test_coloured_beams():
    beams = []
    a = 0
    for color_name, color in color_dict.items():
        beams += [Beam(f"bm{a}", (a, a, a), (a + 1, a + 1, a + 1), "TUB300/200x20", color=color_name)]
        a += 1
        beams += [Beam(f"bm{a}", (a, a, a), (a + 1, a + 1, a + 1), "TUB300/200x20", color=color)]
        a += 1

    a = Assembly("SiteTest", project="projA", user=User("krande")) / (Part("TestBldg") / beams)
    _ = a.to_ifc(file_obj_only=True)
