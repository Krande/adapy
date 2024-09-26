import html
import random

import ipywidgets as widgets


class WebSocketRenderer(widgets.DOMWidget):
    def __init__(self, html_content: str, height: int = 500):
        super().__init__()
        self.unique_id: int = random.randint(0, 2**31 - 1)

        # Inject the unique ID into the HTML content
        self.html_content = html_content.replace(
            "<!--WEBSOCKET_ID_PLACEHOLDER-->", f'<script>window.WEBSOCKET_ID = "{self.unique_id}";</script>'
        )

        # Escape and embed the HTML in the srcdoc of the iframe
        srcdoc = html.escape(self.html_content)

        # Create an IFrame widget wrapped in an HTML widget
        self.html_widget = widgets.HTML(
            f'<div><iframe srcdoc="{srcdoc}" width="100%" height="{height}px" style="border:none;"></iframe></div>'
        )

    def display(self):
        return self.html_widget
