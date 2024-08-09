from dataclasses import dataclass


@dataclass
class CodeAsterOptions:
    use_reduced_integration: bool = False
    default_shell_type: str = "QU4"
