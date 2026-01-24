import ada
from ada import Assembly

#Assembly Mass : 1916.916,  CoG  : (0.500, 2.440, 0.185)

# todo seems like from_genie_xml does not import offsets correctly
a: Assembly = ada.from_genie_xml(r"./files/beams_offset.xml")
cog = a.calculate_cog()
a.show()
print(cog)

assert round(cog.tot_mass,3) == 1916.916
assert round(cog.p.x,3) == 0.500
assert round(cog.p.y,3) == 2.440
assert round(cog.p.z,3) == 0.185