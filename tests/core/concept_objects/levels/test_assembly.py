from ada import Assembly, Part


def test_ex1(bm1, bm2):
    a = Assembly("MyAssembly") / [Part("MyPart") / bm1, bm2]
    p = a.parts["MyPart"]

    assert p.beams.from_name("Bm1")
    assert a.beams.from_name("Bm2")


def test_ex2(bm1, bm2, bm3):
    a = Assembly("MyAssembly") / (Part("MyPart") / [bm1, bm2, bm3])
    p = a.parts["MyPart"]

    assert p.beams.from_name("Bm1")
    assert p.beams.from_name("Bm2")
