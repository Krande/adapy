from ada.fem.formats.fea_config import FrameworkConfig
from ada.fem.formats.usfos.write.writer import to_fem as to_fem_usfos


class UsfosSetup(FrameworkConfig):
    default_pre_processor = to_fem_usfos
