import ada
from ada.api.beams.beam_tapered import TaperTypes

bm = ada.Beam("bm1", (0, 0, 0), (1, 0, 0), "IPE300")
beams = [bm]

bm2 = ada.BeamTapered("bm2", (0, 1, 0), (1, 1, 0), "IPE600", "IPE300")
beams.append(bm2)

bm3 = ada.BeamTapered("bm3", (0, 2, 0), (1, 2, 0), "IPE600", "IPE300", taper_type=TaperTypes.FLUSH_TOP)
beams.append(bm3)

bm4 = ada.BeamTapered("bm4", (0, 3, 0), (1, 3, 0), "IPE600", "IPE300", taper_type=TaperTypes.FLUSH_BOTTOM)
beams.append(bm4)

pl = ada.Plate("pl1", [(0, 0), (1, 0), (1, 3), (0, 3)], 0.01)
plates = [pl]

(ada.Part("Beams") / (beams + plates)).show(renderer="pygfx")
