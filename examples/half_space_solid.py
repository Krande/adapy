import ada


p1 = ada.Point([319.546, 98.29, 510.991])
p2 = ada.Point([319.691, 98.29, 511.136])

bm = ada.Beam("bm1", p1, p2, "SHS80x08")
bool1 = bm.add_boolean(ada.BoolHalfSpace(p1, ada.Direction(0, 0, -1)))
bool2 = bm.add_boolean(ada.BoolHalfSpace(p2, ada.Direction(1, 0, 0)))

a = ada.Assembly("P1") / bm
# a.show()
a.show(stream_from_ifc_store=False)
a.to_ifc("temp/half_space_solid.ifc", validate=True)
