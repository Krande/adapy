from ..abaqus.reader import AbaqusReader


def read_fem(assembly, fem_file, fem_name=None):
    """
    Import a Calculix FEM file using the AbaqusReader (calculix shares the same fem format as abaqus).


    :param assembly: Assembly object
    :param fem_file: Path to Calculix .inp file
    :param fem_name: Name of FEM model
    :type assembly: ada.Assembly
    """
    print("Starting import of Calculix input file")
    reader_obj = AbaqusReader(assembly)
    reader_obj.read_inp_file(fem_file)
