import ada


def test_create_beam_with_property_sets():
    a = ada.Assembly("AssemblyWithProps") / ada.Part("PartWithProps") / ada.Beam("bm1", (0, 0, 0), (1, 0, 0), "IPE300")
    f = a.to_ifc(file_obj_only=True)
    for pset in f.by_type("IfcPropertySet"):
        assert pset.OwnerHistory.is_a() == "IfcOwnerHistory"

    b = ada.from_ifc(f)
    f = b.to_ifc(file_obj_only=True)
    for pset in f.by_type("IfcPropertySet"):
        assert pset.OwnerHistory.is_a() == "IfcOwnerHistory"


def test_write_mixed_metadata_property_sets():
    # Metadata mixing a nested property-set dict with a scalar tag must not
    # crash the IFC writer. The old heuristic keyed off the first value only —
    # a dict sorting first sent the scalar sibling ("Mini") through .items().
    # Nested dicts become their own named sets; scalars land in "Properties".
    bm = ada.Beam("bm1", (0, 0, 0), (1, 0, 0), "IPE300")
    bm.metadata = {"Grouping": {"level": 2}, "STRUCTURE_NAME": "Mini"}
    a = ada.Assembly("A") / (ada.Part("P") / bm)
    f = a.to_ifc(file_obj_only=True)

    psets = {p.Name: p for p in f.by_type("IfcPropertySet")}
    assert "Grouping" in psets
    assert "Properties" in psets
    prop_names = {pr.Name for pr in psets["Properties"].HasProperties}
    assert "STRUCTURE_NAME" in prop_names
