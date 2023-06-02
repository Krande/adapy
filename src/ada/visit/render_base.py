# Using optional renderer pygfx


class JupyterRenderer:
    def __init__(self, obj):
        super().__init__()
        self.obj = obj

    def render(self):
        return self.obj._repr_html_()

    def update(self):
        pass
