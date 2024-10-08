from __future__ import annotations

from typing import Any, Callable

from ada.comms.fb_model_gen import FileTypeDC

from .cli_utils import app


def procedure_decorator(
    input_file_type: FileTypeDC | None = None,
    export_file_type: FileTypeDC | None = None,
    options: dict[str, list[Any]] | None = None,
) -> Callable:
    def wrapper(func: Callable) -> Callable:
        # Apply Typer command if necessary
        if app is None:
            raise ImportError("Typer is not installed")

        app.command()(func)

        func.input_file_type = input_file_type
        func.export_file_type = export_file_type

        return func

    return wrapper
