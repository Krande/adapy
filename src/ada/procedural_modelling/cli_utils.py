try:
    from typer import Typer
except ImportError:
    Typer = None

if Typer is not None:
    app = Typer()
else:
    app = None
