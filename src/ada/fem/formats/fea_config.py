import pathlib
from typing import Any, Callable


class FrameworkConfig:
    default_pre_processor = None
    default_executor = None
    default_post_processor: Callable[[str | pathlib.Path, bool], Any] = None

    @classmethod
    def set_default_pre_processor(cls, pre_processor_func):
        cls.default_pre_processor = pre_processor_func

    @classmethod
    def set_default_executor(cls, executor_func):
        cls.default_executor = executor_func

    @classmethod
    def set_default_post_processor(cls, post_processor_func: Callable[[str | pathlib.Path, bool], Any]):
        cls.default_post_processor = post_processor_func
