from ada.config import logger
from ada.visit.render_pygfx import standalone_viewer

logger.setLevel("INFO")

if __name__ == "__main__":
    standalone_viewer()
