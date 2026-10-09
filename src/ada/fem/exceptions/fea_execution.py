from __future__ import annotations

import os


class FEAnalysisUnableToStart(Exception):
    pass


class FEAnalysisUnsuccessfulError(Exception):
    pass


class FEASolveFailed(FEAnalysisUnsuccessfulError):
    """A solver ran and said the solve failed: raised by the runner instead of reading the results.

    A failed run can leave a result file behind -- Code_Aster's MED file holds whatever was printed before the command
    that stopped, CalculiX's .frd its header -- and reading it gives a partial answer or an opaque reader error. This
    carries what the solver said, by name:

    * ``solver``: ``"code_aster"`` or ``"calculix"``
    * ``code``: the first message code (Code_Aster's ``<FACTOR_11>`` as ``"FACTOR_11"``; for CalculiX, which has no
      codes, the routine of its first ``*ERROR in <routine>`` line, or a short tag such as ``"spooles_singular"``)
    * ``codes``: every distinct code, in order
    * ``file``: the file that says so (Code_Aster's .mess, CalculiX's captured output)
    * ``message``: the solver's message text
    * ``hint``: what is known to cause it and what would fix it, when the code is a known one
    """

    def __init__(
        self,
        solver: str,
        code: str,
        file: str | os.PathLike | None,
        message: str = "",
        hint: str = "",
        codes: tuple[str, ...] | list[str] = (),
    ):
        self.solver = solver
        self.code = code
        self.codes = tuple(codes) or (code,)
        self.file = file
        self.message = message
        self.hint = hint
        text = f"{solver} solve failed: <{code}>"
        if len(self.codes) > 1:
            text += f" (messages: {', '.join(f'<{c}>' for c in self.codes)})"
        text += f" -- see {file}"
        if message:
            text += f"\n{message}"
        if hint:
            text += f"\nCause and fix: {hint}"
        super().__init__(text)
