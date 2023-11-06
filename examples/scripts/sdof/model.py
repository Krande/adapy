import ada


def build_model(name: str):
    p = ada.Part("SpringModel")
    n1 = p.fem.nodes.add(ada.Node((0, 1, 1), nid=1))
    n2 = p.fem.nodes.add(ada.Node((10, 1, 1), nid=2))

    # Create BC
    fs_fix = p.fem.add_set(ada.fem.FemSet("fix", [n1]))
    p.fem.add_bc(ada.fem.Bc("fix_bc", fs_fix, (1, 2, 3, 4, 5, 6)))

    # Create Mass
    fs_point = p.fem.add_set(ada.fem.FemSet("point", [n2]))
    p.fem.add_mass(ada.fem.Mass("mass", fs_point, 1, mass_id=2))

    # Create Spring
    con_section = p.fem.add_connector_section(ada.fem.ConnectorSection("SpringSection", 1, rigid_dofs=[1, 2]))
    p.fem.add_connector(ada.fem.Connector("spring", 1, n1, n2, "BUSHING", con_section))

    a = ada.Assembly(name) / p

    # Create Step
    step = a.fem.add_step(
        ada.fem.StepImplicitDynamic(
            "dynamic",
            dyn_type=ada.fem.StepImplicitDynamic.TYPES_DYNAMIC.TRANSIENT_FIDELITY,
            init_incr=1,
            max_incr=0.1,
            total_incr=1000,
        )
    )
    step.add_load(ada.fem.LoadPoint("force", 1, fs_point, 1))
    step.add_history_output(ada.fem.HistOutput("displ", fs_point, "node", ["U1"]))
    # step.add_bc(fs_fix)

    field = step.field_outputs[0]
    field.int_type = field.TYPES_INTERVAL.INTERVAL
    field.int_value = 100

    # a.to_fem("sdof1_aba", "abaqus", scratch_dir=SCRATCH, overwrite=True)
    # a.to_fem("sdof1_ca", "code_aster", scratch_dir=SCRATCH, overwrite=True)
    return a


if __name__ == "__main__":
    build_model('sdof_local_test')
