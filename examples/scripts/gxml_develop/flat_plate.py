import os
import pathlib
import subprocess

from dotenv import load_dotenv

import ada
from ada.cadit.sat.write.writer import part_to_sat_writer
from ada.visit.renderer_manager import RenderParams

load_dotenv()
start_str = """GenieRules.Compatibility.version = "V8.10-01";
GenieRules.Tolerances.useTolerantModelling = true;
GenieRules.Tolerances.angleTolerance = 2 deg;

GenieRules.Meshing.autoSimplifyTopology = true;
GenieRules.Meshing.eliminateInternalEdges = true;
GenieRules.BeamCreation.DefaultCurveOffset = ReparameterizedBeamCurveOffset();
GenieRules.Geometry.AssemblyType = DualAssembly;
GenieRules.Transformation.DefaultConnectedCopy = false;
"""


def main():
    output_dir = pathlib.Path("temp").resolve().absolute()
    output_dir.mkdir(exist_ok=True)

    dest_sat_file = output_dir / "flat_plate_sesam_10x10.sat"
    dest_gxml_file = output_dir / "flat_plate_sesam_10x10.xml"
    startup_js_file = output_dir / "startup.js"
    workspace = output_dir / "workspace/flat_plate_sesam_10x10"
    workspace.mkdir(parents=True, exist_ok=True)
    # _ = ada.from_genie_xml(output_dir / "exported_mixed_bm_shell.xml")
    beams = []
    plates = []
    pl = ada.Plate("pl", [(0, 0), (10, 0), (10, 10), (0, 10)], 0.01)
    plates.append(pl)
    pl2 = ada.Plate("pl2", [(0, 0), (10, 0), (10, 10), (0, 10)], 0.01, origin=(10, 10, 0))
    plates.append(pl2)
    pl3 = ada.Plate(
        "pl3", [(0, 0), (10, 0), (10, 2), (8, 2), (8, 4), (10, 4), (10, 10), (0, 10)], 0.01, origin=(0, 0, 2)
    )
    plates.append(pl3)
    pl4 = ada.Plate("pl4", [(0, 0), (10, 0), (10, 10), (0, 10)], 0.01, n=(0, 1, 0), xdir=(1, 0, 0))
    plates.append(pl4)
    pl5 = ada.Plate("pl5", [(0, 0), (10, 0), (10, 10), (0, 10)], 0.01, n=(1, 0, 0), xdir=(0, 1, 0))
    plates.append(pl5)
    bm_gen = ada.Counter(prefix="bm")
    beams += ada.Beam.array_from_list_of_segments(pl5.poly.segments3d, "IPE300", name_gen=bm_gen)
    beams += ada.Beam.array_from_list_of_segments(pl.poly.segments3d, "IPE300", name_gen=bm_gen)
    beams += ada.Beam.array_from_list_of_segments(pl3.poly.segments3d, "IPE300", name_gen=bm_gen)

    beams.append(ada.Beam("bm_yUP", (0, 0, 0), (0, 0, 10), "IPE300", up=(0, 1, 0)))
    beams.append(ada.Beam("bm_xUP", (0, 0, 0), (0, 0, 10), "IPE300", up=(1, 0, 0)))
    sec = ada.Section.from_str("IPE300")
    # beams = [ada.Beam("bm", (0, 0, 0), (0, 10, 0), 'IPE300')]
    # beams = []
    a = ada.Assembly() / (ada.Part("MyPart") / plates, ada.Part("MyEmptyPart"), ada.Part("MyBeams") / beams)
    a.show()
    # a.sections.add(sec)
    a.to_genie_xml(dest_gxml_file, embed_sat=True)
    a.to_gltf(output_dir / "flat_plate_sesam_10x10.glb", merge_meshes=True)
    sw = part_to_sat_writer(a)
    sw.write(dest_sat_file)
    xml_import = f'XmlImporter = ImportConceptXml();\nXmlImporter.DoImport("{dest_gxml_file.as_posix()}");'
    sat_import = f'SatImporter = ImportGeometrySat();\nSatImporter.DoImport("{dest_sat_file.as_posix()}");'

    with open(startup_js_file, "w") as f:
        f.write(start_str + "\n")
        f.write(xml_import)

    genie_exe = os.getenv("ADA_GENIE_EXE")
    if genie_exe is None:
        print("Please set the environment variable ADA_GENIE_EXE to the path of the Genie executable")
        return

    args = (
        f'"{genie_exe}" {workspace.absolute().as_posix()} /new /com={startup_js_file.resolve().absolute().as_posix()}'
    )
    # subprocess.run(args, shell=True)

    # If you want to start an external process, you can use the following line:
    # subprocess.Popen(args, shell=True)


if __name__ == "__main__":
    main()
