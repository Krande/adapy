// GeniE V8.13-02 importing a TG300x200x10x15 as adapy's Genie XML writer writes it now
// (adapy_t_section.xml: unsymmetrical_i_section with bfbot = tw + 0.001 mm, tfbot = 0.001 mm, GeniE's
// library T encoding), then mesh and export. Measured: SHARY 1.91397e-3 (adapy's T 1.91384e-3 plus
// delta / tftop = 6.7e-5), IX 4.15999580e-7 (4.16000e-7). Before (genie_v8_13_gxml_import.js) GeniE
// computed adapy's T as an I: SHARY 3.82768e-3.
XmlImporter = ImportConceptXml();
// Written with an absolute path in the probe; GeniE V8.13-02, GenieR.exe <ws> /new /com=<this file> /exit
XmlImporter.DoImport("REPLACE_WITH_ABSOLUTE_DIR/adapy_t_section.xml");
Md = MeshDensity(0.5 m);
Md.setDefault();
Analysis1 = Analysis(true);
Analysis1.add(MeshActivity());
Analysis1.setActive();
Analysis1.execute();
GenieRules.Meshing.superElementType = 1;
ExportMeshFem().DoExport("REPLACE_WITH_ABSOLUTE_DIR/genie_v8_13_gxml_t_T1.FEM");
