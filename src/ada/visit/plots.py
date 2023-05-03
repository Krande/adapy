import os
import pathlib
from typing import Dict, List, Tuple, Union

from ada.config import get_logger

logger = get_logger()


def easy_plotly(
    title: str,
    in_data: Union[
        Tuple[list, list], List[tuple], Dict[str, Union[Tuple[float, float], Dict[str, Tuple[float, float]]]]
    ],
    xlbl: str = "X-axis",
    ylbl: str = "Y-axis",
    xrange: List[Union[float, int]] = None,
    yrange: List[Union[float, int]] = None,
    yaxformat: str = "E",
    xaxformat: str = None,
    legend_pos: Dict[str, float] = None,
    autoreverse=False,
    save_filename: Union[str, pathlib.PurePath, pathlib.Path] = None,
    mode="lines",
    marker="circle",
    traces=None,
    template="plotly_white",
    annotations=None,
    shapes=None,
    renderer="notebook_connected",
    return_widget=True,
    width=1600,
    height=800,
    log_x=False,
    log_y=False,
):
    """
    A Plotly template for quick and easy interactive scatter plotting using some pre-defined values. If you need more
    control of the plotly plot, you are probably better off using plotly directly

    See more information for scatter plots using Plotly at https://plot.ly/python/reference/#scatter

    :param title: Plot title
    :param in_data: tuple (x, y) for single plots or dict {'var1':{'x': [..], 'y': [..] }, 'var2': {..}, etc..}
    :param xlbl: X-axis label
    :param ylbl: Y-axis label
    :param xrange: min and max values of x-axis
    :param yrange: min and max values of y-axis
    :param yaxformat: "none" | "e" | "E" | "power" | "SI" | "B" (default) exponent format of y-axis.
    :param legend_pos: dict(x=-.1, y=1.2)
    :param autoreverse: Autoreverse the X-axis (opposed to inputting the reversed x-list)
    :param save_filename: Abs path to file location or file name of figure.
    :param mode:
    :param marker:
    :param traces: Add plotly traces manually
    :param template: Which plot template. Default is 'plotly_white'. Alternatives are shown below
    :param annotations:
    :param renderer: Which renderer should be used. Default is 'notebook_connected'. See below for alternatives
    :param return_widget:


    Templates:
                'ggplot2', 'seaborn', 'plotly', 'plotly_white', 'plotly_dark', 'presentation', 'xgridoff', 'none'

    renderers:
                'plotly_mimetype', 'jupyterlab', 'nteract', 'vscode', 'notebook', 'notebook_connected', 'kaggle',
                'azure', 'colab', 'cocalc', 'databricks', 'json', 'png', 'jpeg', 'jpg', 'svg', 'pdf', 'browser',
                'firefox', 'chrome', 'chromium', 'iframe', 'iframe_connected', 'sphinx_gallery'

    """
    import plotly.graph_objects as go

    plot_data = extract_plot_data(in_data, mode, marker)

    if traces is not None:
        plot_data += traces

    autorange = "reversed" if autoreverse is True else None
    layout = go.Layout(
        title=title,
        xaxis=dict(
            title=xlbl,
            titlefont=dict(family="Arial, monospace", size=18, color="#7f7f7f"),
            autorange=autorange,
            range=xrange,
            exponentformat=xaxformat,
        ),
        yaxis=dict(
            title=ylbl,
            titlefont=dict(family="Arial, monospace", size=18, color="#7f7f7f"),
            range=yrange,
            exponentformat=yaxformat,
        ),
        legend=legend_pos,
        template=template,
        shapes=shapes,
    )
    if annotations is not None:
        layout["annotations"] = annotations
    try:
        fig = go.FigureWidget(data=plot_data, layout=layout)
    except ImportError as e:
        fig = go.Figure(data=plot_data, layout=layout)
        logger.warning(f"Could not import go.FigureWidget due to ({e}).\nUsing go.Figure instead")

    if log_y is True:
        fig.update_yaxes(type="log", range=yrange, overwrite=True)  # log range: 10^0=1, 10^5=100000
    if log_x is True:
        fig.update_xaxes(type="log", range=xrange, overwrite=True)  # log range: 10^0=1, 10^5=100000

    if save_filename is not None:
        save_plot(fig, save_filename, width, height)
    else:
        if return_widget is True:
            return fig
        fig.show(renderer=renderer)


def save_plot(fig, save_filename, width, height):
    from plotly import io as pio

    filepath = pathlib.Path(save_filename)
    if filepath.suffix == "":
        filepath = filepath.with_suffix(".png")

    dirpath = os.path.dirname(filepath)
    print(f'Saving "{filepath}"')
    if os.path.isdir(dirpath) is False:
        os.makedirs(dirpath)
    pio.write_image(fig, filepath, width=width, height=height)


def extract_plot_data(in_data, mode, marker):
    import plotly.graph_objs as go

    plot_data = []
    if type(in_data) is dict:
        for key in in_data.keys():
            if type(in_data[key]) is dict:
                x_ = in_data[key]["x"]
                y_ = in_data[key]["y"]
            elif type(in_data[key]) in (tuple, list):
                x_ = in_data[key][0]
                y_ = in_data[key][1]
            else:
                raise Exception('Unrecognized input in dict "{}"'.format(type(in_data[key])))

            trace = go.Scatter(
                x=x_,
                y=y_,
                name=key,
                mode=mode,
                marker=dict(symbol=marker),
            )
            plot_data.append(trace)
    elif type(in_data) in [list, tuple]:
        if len(in_data) == 2:
            x, y = in_data
        else:
            x, y = zip(*in_data)
        trace = go.Scatter(
            x=x,
            y=y,
            mode=mode,
            marker=dict(symbol=marker),
        )
        plot_data.append(trace)
    else:
        raise Exception(f'Unrecognized input type "{type(in_data)}" found for "in_data" or "traces"')

    return plot_data
