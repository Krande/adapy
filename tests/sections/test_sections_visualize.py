import unittest

from common import dummy_display

from ada import Section


class VisualizeSections(unittest.TestCase):
    def test_box(self):
        sec = Section("MyBG", from_str="BG800x400x30x40")
        dummy_display(sec)

    def test_ig(self):
        sec = Section("MyIG", from_str="IG800x400x30x40")
        dummy_display(sec)

    def test_angular(self):
        sec = Section("MyHP", from_str="HP180x10")
        dummy_display(sec)

    def test_tubular(self):
        sec = Section("MyTUB", from_str="TUB200x10")
        dummy_display(sec)

    def test_channel(self):
        sec = Section("MyUNP", from_str="UNP200x10")
        dummy_display(sec)

    def test_circular(self):
        sec = Section("MyCirc", from_str="CIRC200")
        dummy_display(sec)


if __name__ == "__main__":
    unittest.main()
