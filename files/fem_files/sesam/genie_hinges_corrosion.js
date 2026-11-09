// Probe for files/fem_files/sesam/genie_hinges_corrosion.xml (GeniE V9.3-00, GenieRuntime --new --com=<this> --exit):
// a rigid hinge freeing both bending rotations (Bm1 end2), a flexible one with two rotational springs (Bm2 end1),
// a plate with 2 mm / 3 mm corrosion on front / back and a beam with 2 mm. probe.xml was renamed to the fixture.

St = MaterialLinear(355e6 Pa, 7850 kg/m^3, 2.1e+11 Pa, 0.3, 1.2e-05 delC^-1, 0.03 N*s/m);
St.setDefault();
Sec = ISection(0.3 m, 0.15 m, 0.0071 m, 0.0107 m);
Sec.setDefault();
Thk = Thickness(0.02 m);
Thk.setDefault();

// hinges: a rigid one freeing the bending rotations, a flexible one with two rotational springs
H_pin = Hinge(1, 1, 1, 1, 0, 0);
H_flex = FlexibleHinge(Fixed, Fixed, Fixed, Fixed, Stiffness(1e6), Stiffness(2e6));
Bm1 = Beam(Point(0 m, 0 m, 0 m), Point(4 m, 0 m, 0 m));
Bm2 = Beam(Point(4 m, 0 m, 0 m), Point(8 m, 0 m, 0 m));
Bm1.setEndHinge(2, H_pin);
Bm2.setEndHinge(1, H_flex);

// corrosion: a plate, 2 mm on its front and 3 mm on its back; a beam, 2 mm
C2 = CorrosionAddition(2 mm);
C3 = CorrosionAddition(3 mm);
Pl1 = Plate(Point(0 m, 2 m, 0 m), Point(4 m, 2 m, 0 m), Point(4 m, 4 m, 0 m), Point(0 m, 4 m, 0 m));
Pl1.front.corrosionAddition = C2;
Pl1.back.corrosionAddition = C3;
Bm3 = Beam(Point(0 m, 6 m, 0 m), Point(4 m, 6 m, 0 m));
Bm3.corrosionAddition = C2;

ExportConceptXml().DoExport("REPLACE_WITH_ABSOLUTE_DIR/probe.xml");
Md = MeshDensity(0.5 m);
Md.setDefault();
Analysis1 = Analysis(true);
Analysis1.add(MeshActivity());
Analysis1.setActive();
Analysis1.execute();
GenieRules.Meshing.superElementType = 1;
ExportMeshFem().DoExport("REPLACE_WITH_ABSOLUTE_DIR/T1.FEM");
