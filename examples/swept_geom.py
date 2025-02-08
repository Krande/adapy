import ada


def straight_sweep():
    sweep_curve = [(0, 0.1, 0.2), (1, 0.1, 0.2)]
    profile = [(0, 0), (1e-2, 0), (0.5e-2, 1e-2)]
    sweep = ada.PrimSweep('sweep', sweep_curve, profile, (1, 0, 0), (0, 1, 0))
    sweep.show(stream_from_ifc_store=True)
    (ada.Assembly() / sweep).to_ifc()


if __name__ == '__main__':
    straight_sweep()
