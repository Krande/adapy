import os
import traceback

from ada.config import logger


def adacpp_switch(alt_function, broken=False):
    def decorator(func):
        def wrapper(*args, **kwargs):
            if os.getenv("USE_ADACPP", "0") == "1" and not broken:
                try:
                    return alt_function(*args, **kwargs)
                except ImportError:
                    logger.error("Failed to import adacpp module. Falling back to adapy.")
                except BaseException as e:
                    logger.error(f"Failed to use the adacpp module, {e}, {traceback.format_exc()}")
                    raise e
            return func(*args, **kwargs)

        return wrapper

    return decorator
