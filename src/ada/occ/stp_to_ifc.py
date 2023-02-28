from OCC.Extend.DataExchange import read_step_file
from OCC.Extend.TopologyUtils import TopologyExplorer


def iter_subshapes(shape):
    t = TopologyExplorer(shape)
    for solid in t.solids():
        yield solid
    for shell in t.shells():
        yield shell
    for face in t.faces():
        yield face


def make_conversion(stp_file):
    for x in iter_subshapes(read_step_file(stp_file, as_compound=False)):
        print(x)


if __name__ == "__main__":
    make_conversion("../../../files/step_files/Ventilator.stp")
