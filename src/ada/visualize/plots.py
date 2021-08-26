import os
import pathlib
import shutil

import plotly.graph_objs as go
from plotly import io as pio


def easy_plotly(
    title,
    in_data,
    xlbl="X-axis",
    ylbl="Y-axis",
    xrange=None,
    yrange=None,
    yaxformat="E",
    legend=None,
    autoreverse=False,
    save_filename=None,
    mode="lines",
    marker="circle",
    traces=None,
    template="plotly_white",
    annotations=None,
    shapes=None,
    renderer="notebook_connected",
    return_widget=True,
):
    """
    A Plotly template for quick and easy interactive scatter plotting using some pre-defined values. If you need more
    control of the plotly plot, you are probably better off using plotly directly

    See https://plot.ly/python/reference/#scatter for a complete list of input for

    :param title: Plot title
    :param in_data: tuple (x, y) for single plots or dict {'var1':{'x': [..], 'y': [..] }, 'var2': {..}, etc..}
    :param xlbl: X-axis label
    :param ylbl: Y-axis label
    :param xrange: min and max values of x-axis
    :param yrange: min and max values of y-axis
    :param yaxformat: "none" | "e" | "E" | "power" | "SI" | "B" (default) exponent format of y-axis.
    :param legend: dict(x=-.1, y=1.2)
    :param autoreverse: Autoreverse the X-axis (opposed to inputting the reversed x-list)
    :param save_filename: Abs path to file location or file name of figure.
    :param mode:
    :param marker:
    :param traces: Add plotly traces manually
    :param template: Which plot template. Default is 'plotly_white'. Alternatives are shown below
    :param annotations:
    :param renderer: Which renderer should be used. Default is 'notebook_connected'. See below for alternatives
    :param return_widget:
    :type title: str
    :type xlbl: str
    :type ylbl: str
    :type xrange: list
    :type yrange: list
    :type yaxformat: str
    :type save_filename: str
    :type mode: str

    Templates:
                'ggplot2', 'seaborn', 'plotly', 'plotly_white', 'plotly_dark', 'presentation', 'xgridoff', 'none'

    renderers:
                'plotly_mimetype', 'jupyterlab', 'nteract', 'vscode', 'notebook', 'notebook_connected', 'kaggle',
                'azure', 'colab', 'cocalc', 'databricks', 'json', 'png', 'jpeg', 'jpg', 'svg', 'pdf', 'browser',
                'firefox', 'chrome', 'chromium', 'iframe', 'iframe_connected', 'sphinx_gallery'

    """

    plot_data = []
    if type(in_data) is dict:
        for key in in_data.keys():
            if type(in_data[key]) is dict:
                x_ = in_data[key]["x"]
                y_ = in_data[key]["y"]
            elif type(in_data[key]) is tuple:
                x_ = in_data[key][0]
                y_ = in_data[key][1]
            else:
                raise Exception('unrecognized input in dict "{}"'.format(type(in_data[key])))

            trace = go.Scatter(
                x=x_,
                y=y_,
                name=key,
                mode=mode,
                marker=dict(symbol=marker),
            )
            plot_data.append(trace)
    elif type(in_data) in [list, tuple]:
        x, y = in_data
        trace = go.Scatter(
            x=x,
            y=y,
            mode=mode,
            marker=dict(symbol=marker),
        )
        plot_data.append(trace)
    else:
        if traces is None:
            raise Exception('No Recognized input type found for "in_data" or "traces"')
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
        ),
        yaxis=dict(
            title=ylbl,
            titlefont=dict(family="Arial, monospace", size=18, color="#7f7f7f"),
            range=yrange,
            exponentformat=yaxformat,
        ),
        legend=legend,
        template=template,
        shapes=shapes,
    )
    if annotations is not None:
        layout["annotations"] = annotations
    fig = go.FigureWidget(data=plot_data, layout=layout)
    # plotly.offline.init_notebook_mode(connected=True)
    if save_filename is not None:
        # fig.show(renderer=renderer)
        filepath = save_filename
        if ".png" not in filepath:
            filepath += ".png"

        dirpath = os.path.dirname(filepath)
        print('Saving "{}" to "{}"'.format(os.path.basename(filepath), dirpath))
        filename = os.path.splitext(filepath)[0].replace(dirpath + "\\", "")
        if os.path.isdir(dirpath) is False:
            os.makedirs(dirpath)
        pio.write_image(fig, save_filename, width=1600, height=800)
        if "\\" not in save_filename:
            output_file = pathlib.Path(f"C:/ADA/temp/{filename}.png")
            if os.path.isfile(output_file) is True:
                shutil.move(output_file, dirpath + "\\" + filename + ".png")
            else:
                print("{} not found".format(output_file))

    else:
        if return_widget is True:
            return fig
        fig.show(renderer=renderer)
