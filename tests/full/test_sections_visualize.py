from ada import Section


def test_box():
    sec = Section("MyBG", from_str="BG800x400x30x40")
    html_str = sec.show(return_as_html_str=True)
    assert html_str is not None


def test_ig():
    sec = Section("MyIG", from_str="IG800x400x30x40")
    html_str = sec.show(return_as_html_str=True)
    assert html_str is not None


def test_angular():
    sec = Section("MyHP", from_str="HP180x10")
    html_str = sec.show(return_as_html_str=True)
    assert html_str is not None


def test_tubular():
    sec = Section("MyTUB", from_str="TUB200x10")
    html_str = sec.show(return_as_html_str=True)
    assert html_str is not None


def test_channel():
    sec = Section("MyUNP", from_str="UNP200x10")
    html_str = sec.show(return_as_html_str=True)
    assert html_str is not None


def test_circular():
    sec = Section("MyCirc", from_str="CIRC200")
    html_str = sec.show(return_as_html_str=True)
    assert html_str is not None
