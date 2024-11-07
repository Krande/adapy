import os
import pathlib
import subprocess

from dotenv import load_dotenv

import ada
from ada.cadit.sat.write.writer import part_to_sat_writer

load_dotenv()


def main():
    output_dir = pathlib.Path("temp").resolve().absolute()
    output_dir.mkdir(exist_ok=True)

    dest_sat_file = output_dir / "flat_plate_sesam_10x10.sat"
    dest_gxml_file = output_dir / "flat_plate_sesam_10x10.xml"
    exported_flat = output_dir / "exported_flat_plate_10x10.xml"
    res = ada.from_genie_xml(exported_flat)
    startup_js_file = output_dir / 'startup.js'
    workspace = output_dir / "workspace/flat_plate_sesam_10x10"
    workspace.mkdir(parents=True, exist_ok=True)

    pl = ada.Plate("pl", [(0, 0), (10, 0), (10, 10), (0, 10)], 0.1)
    pl2 = ada.Plate("pl2", [(0, 0), (10, 0), (10, 10), (0, 10)], 0.1, origin=(10, 10, 0))
    pl3 = ada.Plate("pl3", [(0, 0), (10, 0), (10, 2), (8, 2), (8, 4), (10, 4),(10,10), (0, 10)], 0.1, origin=(0, 0, 2))
    plates = [pl] #, pl2, pl3]
    a = ada.Assembly() / plates
    a.to_genie_xml(dest_gxml_file, embed_sat=True)

    sw = part_to_sat_writer(a)
    sw.write(dest_sat_file)
    xml_import = f'XmlImporter = ImportConceptXml();\nXmlImporter.DoImport("{dest_gxml_file.as_posix()}");'
    sat_import = f'SatImporter = ImportGeometrySat();\nSatImporter.DoImport("{dest_sat_file.as_posix()}");'

    with open(startup_js_file, 'w') as f:
        f.write(xml_import)

    genie_exe = os.getenv("ADA_GENIE_EXE")
    if genie_exe is None:
        print("Please set the environment variable ADA_GENIE_EXE to the path of the Genie executable")
        return

    args = f"\"{genie_exe}\" {workspace.absolute().as_posix()} /new /com={startup_js_file.resolve().absolute().as_posix()}"
    #subprocess.run(args, shell=True)

    # If you want to start an external process, you can use the following line:
    os.system(args)


if __name__ == '__main__':
    main()
