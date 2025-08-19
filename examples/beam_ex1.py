from ada.param_models.fem_models import beam_ex1

a = beam_ex1()

result = a.to_fem("MyCantilever_code_aster", "code_aster", overwrite=True, execute=True)
result.show()
