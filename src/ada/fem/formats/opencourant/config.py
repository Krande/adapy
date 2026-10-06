from ada.fem.formats.fea_config import FrameworkConfig
from ada.fem.formats.opencourant.execute import run_opencourant
from ada.fem.formats.opencourant.results.container import read_opencourant_results
from ada.fem.formats.opencourant.write.writer import to_fem as opencourant_to_fem


class OpenCourantSetup(FrameworkConfig):
    default_pre_processor = opencourant_to_fem
    default_executor = run_opencourant
    default_post_processor = read_opencourant_results
