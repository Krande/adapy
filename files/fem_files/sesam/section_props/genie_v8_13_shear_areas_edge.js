// Shear-area reference probe, edge cases: the neutral axis inside a flange.
St = MaterialLinear(355e6 Pa, 7850 kg/m^3, 2.1e+11 Pa, 0.3, 1.2e-05 delC^-1, 0.03 N*s/m);
St.setDefault();
E1_TEETHICK = UnsymISection(0.1 m, 0.005 m, 0.3 m, 0.15 m, 0.05 m, 0.005 m, 0.0025 m, 0.005 m);
E2_ANGFLNA = LSection(0.06 m, 0.3 m, 0.005 m, 0.04 m);
E1_TEETHICK.setDefault();
Bm_E1 = Beam(Point(0 m, 0 m, 0 m), Point(1 m, 0 m, 0 m));
E2_ANGFLNA.setDefault();
Bm_E2 = Beam(Point(0 m, 2 m, 0 m), Point(1 m, 2 m, 0 m));
Md = MeshDensity(0.5 m);
Md.setDefault();
Analysis1 = Analysis(true);
Analysis1.add(MeshActivity());
Analysis1.setActive();
Analysis1.execute();
GenieRules.Meshing.superElementType = 1;
// Written with an absolute path in the probe; GeniE V8.13-02, GenieR.exe <ws> /new /com=<this file> /exit
ExportMeshFem().DoExport("REPLACE_WITH_ABSOLUTE_DIR/genie_v8_13_shear_areas_edge_T1.FEM");
