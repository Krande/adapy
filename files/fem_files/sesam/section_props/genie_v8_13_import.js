// What GeniE V8.13-02 makes of Sesam profile cards on import (Krande/adapy#440 review): import
// adapy_profile_cards_for_genie_import.FEM, mesh, export. That deck was written by adapy and then
// edited per section (GEONO, name):
//   1 T_STUB   TG300x200x10x15 as adapy wrote a T before: GIORH BB = TY, TB = TT (web-wide stub)
//   2 T_GENIE  TG300x200x10x16 with GIORH in GeniE's library T encoding: BB = TY + 1e-6, TB = 1e-6
//   3 L_K1     L 200 x 100, web 10, flange 14, GLSEC K = 1 (as adapy wrote every angle before)
//   4 L_K0     L 200 x 100, web 10, flange 15, GLSEC K = 0
//   5 I_SF     IPE300, GIORH SFY 0.5 / SFZ 0.8, GBEAMG SHARY/SHARZ x 0.5 / 0.8
//   6 I_IX10   IPE330, GBEAMG IX x 10, COMP 0
//   7 I_SF0    IPE360, GIORH SFY 0 / SFZ 0, GBEAMG unchanged
//   8 I_COMP1  IPE400, GBEAMG IX x 10, COMP 1
// Measured: GeniE recomputes every GBEAMG from the profile card (IX x 10 is gone, with COMP 0 and 1),
// keeps SFY/SFZ and applies them (SFY 0 gives SHARY 0), and writes K = 0 for both angles with the
// same SHCENY sign (K = 1 is not mirrored).
FemImporter = ImportMeshFem();
FemImporter.setPropertyDirect(true);
// Written with an absolute path in the probe; GeniE V8.13-02, GenieR.exe <ws> /new /com=<this file> /exit
FemImporter.DoImport("REPLACE_WITH_ABSOLUTE_DIR/adapy_profile_cards_for_genie_import.FEM");
XmlExporter = ExportConceptXml();
XmlExporter.DoExport("REPLACE_WITH_ABSOLUTE_DIR/genie_v8_13_import_concept.xml");  // beam local systems: all identical
Md = MeshDensity(0.5 m);
Md.setDefault();
Analysis1 = Analysis(true);
Analysis1.add(MeshActivity());
Analysis1.setActive();
Analysis1.execute();
GenieRules.Meshing.superElementType = 1;
ExportMeshFem().DoExport("REPLACE_WITH_ABSOLUTE_DIR/genie_v8_13_import_T1.FEM");
