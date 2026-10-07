// What GeniE V8.13-02 makes of adapy's Genie XML sections (Krande/adapy#440 review): import
// adapy_factored_sections.xml, mesh, export. The XML was written by adapy's to_genie_xml from GeniE's
// six factored sections (L3_*, SFY 0.5 / SFZ 0.8, read from genie_v8_13_review437_T1.FEM) and a
// TG300x200x10x15 (written as adapy wrote a T: unsymmetrical_i_section with bfbot = tw, tfbot = tftop).
// Measured: GeniE keeps sfy/sfz and its GBEAMG equals its own factored sections' to 1.2e-7; from the T it
// computes an I (SHARY 2.000 x the T's).
XmlImporter = ImportConceptXml();
// Written with an absolute path in the probe; GeniE V8.13-02, GenieR.exe <ws> /new /com=<this file> /exit
XmlImporter.DoImport("REPLACE_WITH_ABSOLUTE_DIR/adapy_factored_sections.xml");
Md = MeshDensity(0.5 m);
Md.setDefault();
Analysis1 = Analysis(true);
Analysis1.add(MeshActivity());
Analysis1.setActive();
Analysis1.execute();
GenieRules.Meshing.superElementType = 1;
ExportMeshFem().DoExport("REPLACE_WITH_ABSOLUTE_DIR/genie_v8_13_gxml_import_T1.FEM");
