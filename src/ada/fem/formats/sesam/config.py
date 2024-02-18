from ada.fem.formats.fea_config import FrameworkConfig
from ada.fem.formats.sesam.execute import run_sesam
from ada.fem.formats.sesam.results.read_sif import read_sin_file
from ada.fem.formats.sesam.write.writer import to_fem as to_fem_sesam


class SesamSetup(FrameworkConfig):
    default_pre_processor = to_fem_sesam
    default_executor = run_sesam
    default_post_processor = read_sin_file
