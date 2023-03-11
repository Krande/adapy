from ada import Section


def test_box(dummy_display):
    sec = Section("MyBG", from_str="BG800x400x30x40")
    dummy_display(sec)


def test_ig(dummy_display):
    sec = Section("MyIG", from_str="IG800x400x30x40")
    dummy_display(sec)


def test_angular(dummy_display):
    sec = Section("MyHP", from_str="HP180x10")
    dummy_display(sec)


def test_tubular(dummy_display):
    sec = Section("MyTUB", from_str="TUB200x10")
    dummy_display(sec)


def test_channel(dummy_display):
    sec = Section("MyUNP", from_str="UNP200x10")
    dummy_display(sec)


def test_circular(dummy_display):
    sec = Section("MyCirc", from_str="CIRC200")
    dummy_display(sec)
