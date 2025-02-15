import os
from ipywidgets import DOMWidget
from traitlets import Unicode

# Get the path to the `jupyter-dist/` directory
MODULE_DIR = os.path.dirname(__file__)
JUPYTER_DIST_DIR = os.path.join(MODULE_DIR, "jupyter-dist")

class AdapyViewerWidget(DOMWidget):
    _view_name = Unicode("AdapyViewerWidgetView").tag(sync=True)
    _model_name = Unicode("AdapyViewerWidgetModel").tag(sync=True)
    _view_module = Unicode("adapy-viewer-widget").tag(sync=True)
    _model_module = Unicode("adapy-viewer-widget").tag(sync=True)
    _view_module_version = Unicode("0.1.0").tag(sync=True)
    _model_module_version = Unicode("0.1.0").tag(sync=True)

    message = Unicode("").tag(sync=True)

    def send_message(self, msg: str):
        """Send a message to the React frontend"""
        self.message = msg

    @classmethod
    def get_jupyter_dist(cls):
        """Return the absolute path to the Jupyter frontend files"""
        return JUPYTER_DIST_DIR
