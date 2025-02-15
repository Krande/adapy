import os
from ipywidgets import DOMWidget
from traitlets import Unicode

# Get the path to the `jupyter-dist/` directory
MODULE_DIR = os.path.dirname(__file__)

class AdapyViewerWidget(DOMWidget):
    _view_name = Unicode("AdapyViewerWidgetView").tag(sync=True)
    _model_name = Unicode("AdapyViewerWidgetModel").tag(sync=True)
    _view_module = Unicode("adapy_viewer_widget").tag(sync=True)
    _model_module = Unicode("adapy_viewer_widget").tag(sync=True)
    _view_module_version = Unicode("0.1.0").tag(sync=True)
    _model_module_version = Unicode("0.1.0").tag(sync=True)

    message = Unicode("").tag(sync=True)

    def send_message(self, msg: str):
        """Send a message to the React frontend"""
        self.message = msg