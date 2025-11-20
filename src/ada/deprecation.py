import functools
import warnings
from typing import Callable, Type


def deprecated(reason: str):
    """
    A decorator to mark functions or classes as deprecated.
    Emits a warning when the function or class is used, including the module path.

    :param reason: Explanation of why the function/class is deprecated.
    """

    def decorator(obj: Callable | Type):
        full_path = f"{obj.__module__}.{obj.__qualname__}"

        if isinstance(obj, type):
            # Class deprecation
            class Wrapper(obj):
                def __init__(self, *args, **kwargs):
                    warnings.warn(f"Class '{full_path}' is deprecated: {reason}", DeprecationWarning, stacklevel=2)
                    super().__init__(*args, **kwargs)

            return Wrapper
        else:
            # Function deprecation
            @functools.wraps(obj)
            def wrapped(*args, **kwargs):
                warnings.warn(f"Function '{full_path}' is deprecated: {reason}", DeprecationWarning, stacklevel=2)
                return obj(*args, **kwargs)

            return wrapped

    return decorator
