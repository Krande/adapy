from ada.fem import Load, LoadPressure


def write_load(load: Load) -> str:
    load_str_map = {
        Load.TYPES.GRAVITY: gravity_load_str,
        Load.TYPES.ACC: acc_load_str,
        Load.TYPES.PRESSURE: pressure_load_str,
    }

    load_str_writer = load_str_map.get(load.type, None)

    if load_str_writer is None:
        raise NotImplementedError(f'Load type "{load.type}"')

    return load_str_writer(load)


def gravity_load_str(load: Load) -> str:
    return f"""{load.name} = AFFE_CHAR_MECA(
    MODELE=model, PESANTEUR=_F(DIRECTION=(0.0, 0.0, 1.0), GRAVITE={load.magnitude})
)"""


def acc_load_str(load: Load) -> str:
    acc_dir_str = f"({','.join(load.acc_vector)})"
    return f"""{load.name} = AFFE_CHAR_MECA(
    MODELE=model, PESANTEUR=_F(DIRECTION={acc_dir_str}, GRAVITE={load.magnitude})
)"""


def pressure_load_str(load: LoadPressure) -> str:
    return f"""{load.name} = AFFE_CHAR_MECA(
    MODELE=model, FORCE_FACE=_F(FY={load.magnitude}, GROUP_MA=('{load.surface.name}'))
)"""
