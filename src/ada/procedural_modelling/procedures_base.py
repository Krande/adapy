from __future__ import annotations

from typing import Any, Callable

from ada.comms.fb_model_gen import FileTypeDC

from .cli_utils import app


class BaseDecorator:
    def __init__(
        self,
        inputs: dict[str, FileTypeDC] | None = None,
        outputs: dict[str, FileTypeDC] | None = None,
        options: dict[str, list[Any]] | None = None,
    ) -> None:
        self.inputs = inputs
        self.outputs = outputs
        self.options = options or {}

    def __call__(self, func: Callable) -> Callable:
        if app is None:
            raise ImportError("Typer is not installed")

        # Apply Typer command
        app.command()(func)

        # Attach attributes to the function
        func.inputs = self.inputs
        func.outputs = self.outputs

        return func


class ProcedureDecorator(BaseDecorator):
    def __init__(
        self,
        inputs: dict[str, FileTypeDC] | None = None,
        outputs: dict[str, FileTypeDC] | None = None,
        options: dict[str, list[Any]] | None = None,
    ) -> None:
        super().__init__(inputs=inputs, outputs=outputs, options=options)


class ComponentDecorator(BaseDecorator):
    def __init__(
        self,
        inputs: dict[str, FileTypeDC] | None = None,
        outputs: dict[str, FileTypeDC] | None = None,
        options: dict[str, list[Any]] | None = None,
    ) -> None:
        super().__init__(inputs=inputs, outputs=outputs, options=options)

    def __call__(self, func: Callable) -> Callable:
        # You can override the base behavior here if needed
        func = super().__call__(func)
        # Additional behavior specific to ComponentDecorator
        return func
