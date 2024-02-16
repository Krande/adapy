from ada.fem.formats.fea_config import FrameworkConfig
from ada.fem.formats.sesam import run_sesam
from ada.fem.formats.sesam.results.read_sif import read_sin_file


class SesamSetup(FrameworkConfig):
    default_executor = run_sesam
    default_post_processor = read_sin_file
