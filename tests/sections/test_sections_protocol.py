import unittest

from ada import Section
from ada.concepts.containers import Sections


class TestContainerProtocol(unittest.TestCase):
    def setUp(self) -> None:
        self.sec = Section("MyBGSec", from_str="BG800x400x30x40")
        self.sec2 = Section("MyBGSec2", from_str="BG800x400x30x50")

    def test_negative_contained(self):
        sec_collection = Sections([self.sec])
        self.assertFalse(self.sec2 in sec_collection)

    def test_positive_contained(self):
        sec_collection = Sections([self.sec, self.sec2])
        self.assertTrue(self.sec2 in sec_collection)


if __name__ == "__main__":
    unittest.main()
