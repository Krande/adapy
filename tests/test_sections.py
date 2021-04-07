import unittest

from ada import Section
from ada.core.containers import Sections


class VisualizeSections(unittest.TestCase):
    def test_box(self):
        sec = Section("MyBG", from_str="BG800x400x30x40")
        sec._repr_html_()

    def test_ig(self):
        sec = Section("MyIG", from_str="IG800x400x30x40")
        sec._repr_html_()

    def test_angular(self):
        sec = Section("MyHP", from_str="HP180x10")
        sec._repr_html_()

    def test_tubular(self):
        sec = Section("MyTUB", from_str="TUB200x10")
        sec._repr_html_()

    # TODO: Add support for visualizing circular and channel profiles
    # def test_circular(self):
    #     sec = Section('MyCirc', from_str='CIRC200')
    #     sec._repr_html_()
    #
    # def test_channel(self):
    #     sec = Section('MyUNP', from_str='UNP200x10')
    #     sec._repr_html_()


class PropertiesTestCase(unittest.TestCase):
    def test_box(self):
        sec = Section("MyBGSec", from_str="BG800x400x30x40")
        sec.properties.calculate()
        sp = sec.properties

        # Complete this when
        assert sp.Ax == 0.0752

    def test_tubular(self):
        sec = Section("MyTUBsec", from_str="TUB375x35")
        sec.properties.calculate()
        sp = sec.properties

        # Complete this when
        assert sp.Ax == 0.07861835615608465


class TestContainerProtocol(unittest.TestCase):
    def test_negative_contained(self):
        sec = Section("MyBGSec", from_str="BG800x400x30x40")
        sec_collection = Sections([sec])
        sec2 = Section("MyBGSec", from_str="BG800x400x30x50")
        self.assertFalse(sec2 in sec_collection)


if __name__ == "__main__":
    unittest.main()
