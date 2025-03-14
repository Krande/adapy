import html

import ipywidgets as widgets
from IPython import display


class WebSocketRenderer(widgets.DOMWidget):
    def __init__(self, html_content: str, height: int = 500, unique_id: int = None, ws_port=None):
        super().__init__()
        html_inject_str = ""
        if unique_id is not None:
            self.unique_id = unique_id
            html_inject_str += f'<script>window.WEBSOCKET_ID = "{self.unique_id}";</script>\n'
        if ws_port is not None:
            self.ws_port = ws_port
            html_inject_str += f"<script>window.WEBSOCKET_PORT = {self.ws_port};</script>"
        # Inject the unique ID into the HTML content
        self.html_content = html_content.replace("<!--STARTUP_CONFIG_PLACEHOLDER-->", html_inject_str)

        # Escape and embed the HTML in the srcdoc of the iframe
        srcdoc = html.escape(self.html_content)

        # Create an IFrame widget wrapped in an HTML widget
        self.html_widget = display.HTML(
            f'<div><iframe srcdoc="{srcdoc}" width="100%" height="{height}px" style="border:none;"></iframe></div>'
        )

    def display(self):
        return self.html_widget
