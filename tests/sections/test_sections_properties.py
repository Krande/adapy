import unittest

from ada import Section


class PropertiesTestCase(unittest.TestCase):
    # TODO: This should test for all properties on all profiles
    def test_box(self):
        sec = Section("MyBGSec", from_str="BG800x400x30x40")
        sec.properties.calculate()
        sp = sec.properties

        assert sp.Ax == 0.0752

    def test_tubular(self):
        sec = Section("MyTUBsec", from_str="TUB375x35")
        sec.properties.calculate()
        sp = sec.properties

        assert sp.Ax == 0.07861835615608465


if __name__ == "__main__":
    unittest.main()
