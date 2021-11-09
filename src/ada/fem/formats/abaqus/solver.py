from dataclasses import dataclass


class StabilizeTypes:
    ENERGY = "energy"
    DAMPING = "damping"
    CONTINUE = "continue"


@dataclass
class Stabilize:
    factor: float
    allsdtol: float
    stabilize_type: StabilizeTypes = StabilizeTypes.ENERGY
    energy: float = None
    damping: float = None
    stabilize_continue: bool = True

    def to_input_str(self):
        st = StabilizeTypes
        stable_map = {
            st.ENERGY: f"stabilize={self.factor}, allsdtol={self.allsdtol}",
            st.DAMPING: f"stabilize, factor={self.factor}, allsdtol={self.allsdtol}",
            st.CONTINUE: "stabilize, continue=ON",
        }
        stabilize_str = stable_map.get(self.stabilize_type, None)
        if stabilize_str is None:
            raise ValueError(f'Unrecognized stabilization type "{self.stabilize_type}"')

        return stabilize_str


@dataclass
class AbaqusStepOptions:
    init_accel_calc: bool = True
    restart_int: int = None
    unsymm: bool = False
    stabilize: Stabilize = None

    """
    :param init_accel_calc: Calculate Initial acceleration in the beginning of the step
    :param restart_int: Restart interval
    :param unsymm: Unsymmetric Matrix storage (default=False)
    :param stabilize: Default=None. Abaqus CAE defaults to stabilize=0.0002 and allsdtol=0.05
    """
