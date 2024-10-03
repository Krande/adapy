from __future__ import annotations

from typing import Callable

from ada.comms.fb_model_gen import FileTypeDC
from .cli_utils import app


def procedure_decorator(
        input_file_type: FileTypeDC | None = None,
        export_file_type: FileTypeDC | None = None,
) -> Callable:
    def wrapper(func: Callable) -> Callable:
        # Apply Typer command if necessary
        if app is None:
            raise ImportError("Typer is not installed")
        app.command()(func)

        # Set custom attributes if needed
        func.input_file_type = input_file_type
        func.export_file_type = export_file_type

        # Return the function casted to the correct type
        return func

    return wrapper
