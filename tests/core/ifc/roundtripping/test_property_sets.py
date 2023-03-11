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
