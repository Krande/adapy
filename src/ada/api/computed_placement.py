from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Iterable, Optional, Tuple

if TYPE_CHECKING:
    from ada import Direction


# Cache the origin normalization
@lru_cache(maxsize=5000)
def normalize_origin_cached(origin_input: Optional[Tuple[float, float, float]]) -> Tuple[float, float, float]:
    """
    Cached origin normalization.
    """

    if origin_input is None:
        return (0.0, 0.0, 0.0)
    elif origin_input == (0.0, 0.0, 0.0):
        return (0.0, 0.0, 0.0)  # Return the common case
    else:
        return tuple(float(x) for x in origin_input)


@dataclass(frozen=True)
class ComputedPlacement:
    """
    Immutable computation handler for Placement objects.
    All expensive computations are cached at the placement value level.
    """

    # Immutable placement values - these are the cache keys
    xdir: Direction
    ydir: Direction
    zdir: Direction


# Factory function with LRU cache for creating/reusing ComputedPlacement instances
@lru_cache(maxsize=10000)
def get_computed_placement_cached(
    xdir_input: Tuple[float, float, float] | None,
    ydir_input: Tuple[float, float, float] | None,
    zdir_input: Tuple[float, float, float] | None,
) -> ComputedPlacement:
    """
    Factory function that returns cached ComputedPlacement instances.
    This ensures identical placement values return the same instance.
    """
    # Use cached expensive computations
    from ada import Direction
    from ada.core.vector_transforms import compute_orientation_vec

    xdir_tuple, ydir_tuple, zdir_tuple = compute_orientation_vec(xdir_input, ydir_input, zdir_input)

    return ComputedPlacement(
        xdir=Direction(*xdir_tuple),
        ydir=Direction(*ydir_tuple),
        zdir=Direction(*zdir_tuple),
    )


# Main function that processes raw placement data with caching
def create_computed_placement_from_placement(
    xdir: Iterable[float] | Direction | None,
    ydir: Iterable[float] | Direction | None,
    zdir: Iterable[float] | Direction | None,
) -> ComputedPlacement:
    """
    Create a ComputedPlacement from a Placement object.
    The expensive computations are cached based on raw input values.
    """

    # Convert raw inputs to immutable tuples for caching
    xdir_input = tuple(float(x) for x in xdir) if xdir is not None else None
    ydir_input = tuple(float(x) for x in ydir) if ydir is not None else None
    zdir_input = tuple(float(x) for x in zdir) if zdir is not None else None

    # Use cached factory function
    return get_computed_placement_cached(
        xdir_input=xdir_input,
        ydir_input=ydir_input,
        zdir_input=zdir_input,
    )
