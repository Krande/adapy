from ada.fem.formats.code_aster.execute import run_code_aster
from ada.fem.formats.code_aster.results.read_rmed_results import read_rmed_file
from ada.fem.formats.code_aster.write.writer import to_fem as to_fem_code_aster
from ada.fem.formats.fea_config import FrameworkConfig


class CodeAsterSetup(FrameworkConfig):
    default_pre_processor = to_fem_code_aster
    default_executor = run_code_aster
    default_post_processor = read_rmed_file
