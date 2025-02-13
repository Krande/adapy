import ada
from ada.comms.fb_wrap_model_gen import SceneOperationsDC, SceneDC
from ada.visit.renderer_manager import RenderParams


def main():
    bm1 = ada.Beam('bm1', (0,0,0), (1,0,0), 'IPE300')
    bm1.show()

    # These should append to the existing scene
    bm2 = ada.Beam('bm2', (0,1,0), (1,1,0), 'IPE300')
    bm2.show(params_override=RenderParams(scene=SceneDC(operation=SceneOperationsDC.ADD)), liveness_timeout=1000)

    bm3 = ada.Beam('bm3', (0,2,1), (1,2,0), 'IPE300')
    bm3.show(params_override=RenderParams(scene=SceneDC(operation=SceneOperationsDC.ADD)), liveness_timeout=1000)


if __name__ == '__main__':
    main()