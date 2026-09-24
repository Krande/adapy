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

    def to_params(self) -> list[tuple]:
        """The ``*Static`` parameters, as ``(name, value)`` pairs (``None`` for a flag)."""
        st = StabilizeTypes
        stable_map = {
            st.ENERGY: [("stabilize", self.factor), ("allsdtol", self.allsdtol)],
            st.DAMPING: [("stabilize", None), ("factor", self.factor), ("allsdtol", self.allsdtol)],
            st.CONTINUE: [("stabilize", None), ("continue", "ON")],
        }
        params = stable_map.get(self.stabilize_type, None)
        if params is None:
            raise ValueError(f'Unrecognized stabilization type "{self.stabilize_type}"')

        return params


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
