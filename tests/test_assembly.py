import copy
import unittest

from ada import Assembly, Beam, Part, Section


class TestEqualityProtocol(unittest.TestCase):
    def setUp(self) -> None:
        self.bm1 = Beam("Bm1", (0, 0, 0), (1, 0, 0), "IPE300")
        self.bm2 = Beam("Bm2", (0, 0, 2), (1, 0, 2), "IPE300")
        self.bm3 = Beam("Bm3", (0, 0, 4), (1, 0, 4), "IPE300")

        self.p1 = Part("my_part1")
        self.p2 = Part("my_part2")
        self.p21 = Part("my_part2_subpart1")
        self.p22 = Part("my_part2_subpart2")
        self.p3 = Part("my_part2_subpart1")
        self.p4 = Part("my_part3_subpart1")

        self.secvar = dict(sec_type="IG", h=0.8, w_top=0.2, w_btn=0.2, t_fbtn=0.01, t_ftop=0.01, t_w=0.01)
        self.sec1 = Section(name="sec1", **self.secvar)

    def test_section_equal(self):
        sec1 = self.sec1
        sec2 = copy.deepcopy(sec1)
        sec2.name = "sec2"
        sec3 = Section(name="sec3", **self.secvar)
        list_of_secs = [sec1, sec2, sec3]
        self.assertTrue(sec1 == sec1)
        self.assertTrue(sec1 in list_of_secs)
        self.assertFalse(Section(name="sec4", **self.secvar) in list_of_secs)

    def test_parts_list(self):
        a = Assembly("MyAssembly")

        # Level 1
        part = a.add_part(self.p1)
        a.add_part(self.p2)

        # Level 2
        part2 = part.add_part(self.p21)
        part.add_part(self.p22)

        # Level 3
        subpart3 = part2.add_part(self.p3)

        # Level 4
        subpart3.add_part(self.p4)

        list_of_ps = a.get_all_parts_in_assembly()

        self.assertEqual(len(list_of_ps), 6)

        self.assertEqual(len(a.parts[self.p1.name].parts), 2)
        self.assertEqual(len(a.parts[self.p1.name].parts[self.p21.name].parts), 1)
        self.assertEqual(len(a.parts[self.p1.name].parts[self.p21.name].parts[self.p3.name].parts), 1)

    def test_ex1(self):
        bm1, bm2 = self.bm1, self.bm2
        a = Assembly("MyAssembly") / [Part("MyPart") / bm1, bm2]
        p = a.parts["MyPart"]
        assert p.beams.from_name("Bm1")
        assert a.beams.from_name("Bm2")

    def test_ex2(self):
        bm1, bm2, bm3 = self.bm1, self.bm2, self.bm3
        a = Assembly("MyAssembly") / (Part("MyPart") / [bm1, bm2, bm3])
        p = a.parts["MyPart"]
        assert p.beams.from_name("Bm1")
        assert p.beams.from_name("Bm2")


if __name__ == "__main__":
    unittest.main()
