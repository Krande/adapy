from ada.config import Settings

test_folder = Settings.test_dir / "sesam"


def test_write_ff():
    from ada.fem.formats.sesam.writer import write_ff

    flag = "TDMATER"
    data = [
        (1, 1, 0, 0),
        (83025, 4, 0, 3),
        (0.4870624787676558, 0.4870624787676558, 0.4870624787676558, 0.4870624787676558),
    ]
    test_str = write_ff(flag, data)
    fflag = "BEUSLO"
    ddata = [
        (1, 1, 0, 0),
        (83025, 4, 0, 3),
        (0.4870624787676558, 0.4870624787676558, 0.4870624787676558, 0.4870624787676558),
    ]
    test_str += write_ff(fflag, ddata)
    print(test_str)
