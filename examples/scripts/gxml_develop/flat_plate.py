import os
import pathlib
import subprocess

from dotenv import load_dotenv

import ada
from ada.cadit.sat.write.writer import part_to_sat_writer

load_dotenv()


def main():
    output_dir = pathlib.Path("temp")
    output_dir.mkdir(exist_ok=True)

    dest_sat_file = output_dir / "flat_plate_sesam_10x10.sat"
    startup_js_file = output_dir / 'startup.js'
    workspace = output_dir / "workspace/flat_plate_sesam_10x10"
    workspace.mkdir(parents=True, exist_ok=True)

    pl = ada.Plate("pl", [(0, 0), (10, 0), (10, 10), (0, 10)], 0.1)
    pl2 = ada.Plate("pl2", [(0, 0), (10, 0), (10, 10), (0, 10)], 0.1, origin=(10, 10, 0))
    pl3 = ada.Plate("pl3", [(0, 0), (10, 0), (10, 10), (0, 10)], 0.1, origin=(0, 0, 2))

    a = ada.Assembly() / (pl, pl3, pl2)
    sw = part_to_sat_writer(a)
    sw.write(dest_sat_file)

    with open(startup_js_file, 'w') as f:
        f.write("SatImporter = ImportGeometrySat();\n")
        f.write(f'SatImporter.DoImport("{dest_sat_file.resolve().absolute().as_posix()}");')

    genie_exe = os.getenv("ADA_GENIE_EXE")
    if genie_exe is None:
        print("Please set the environment variable ADA_GENIE_EXE to the path of the Genie executable")
        return

    args = f"\"{genie_exe}\" {workspace.absolute().as_posix()} /new /com={startup_js_file.resolve().absolute().as_posix()}"
    subprocess.run(args, shell=True)

    # If you want to start an external process, you can use the following line:
    # os.system(args)


if __name__ == '__main__':
    main()
