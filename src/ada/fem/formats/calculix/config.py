from ada.fem.formats.calculix.execute import run_calculix
from ada.fem.formats.calculix.results.read_frd_file import read_from_frd_file_proto
from ada.fem.formats.calculix.write.writer import to_fem as calculix_to_fem
from ada.fem.formats.fea_config import FrameworkConfig


class CalculixSetup(FrameworkConfig):
    default_pre_processor = calculix_to_fem
    default_executor = run_calculix
    default_post_processor = read_from_frd_file_proto
