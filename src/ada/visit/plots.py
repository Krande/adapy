from __future__ import annotations

import os
import pathlib
from typing import TYPE_CHECKING, Dict, List, Tuple, Union

from ada.config import get_logger

if TYPE_CHECKING:
    from ada.sections import Section

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
    axis_font = dict(family="Arial, monospace", size=18, color="#7f7f7f")
    layout = go.Layout(
        title=title,
        xaxis=dict(
            title=dict(text=xlbl, font=axis_font),
            autorange=autorange,
            range=xrange,
            exponentformat=xaxformat,
        ),
        yaxis=dict(
            title=dict(text=ylbl, font=axis_font),
            range=yrange,
            exponentformat=yaxformat,
        ),
        legend=legend_pos,
        template=template,
        shapes=shapes,
    )
    if annotations is not None:
        layout["annotations"] = annotations

    fig = go.Figure(data=plot_data, layout=layout)

    if log_y is True:
        fig.update_yaxes(type="log", range=yrange, overwrite=True)  # log range: 10^0=1, 10^5=100000
    if log_x is True:
        fig.update_xaxes(type="log", range=xrange, overwrite=True)  # log range: 10^0=1, 10^5=100000

    if save_filename is not None:
        if isinstance(save_filename, str):
            save_filename = pathlib.Path(save_filename)
        save_filename.parent.mkdir(parents=True, exist_ok=True)
        # Timeout issues with kaleido on win10 -> https://github.com/plotly/Kaleido/issues/110
        try:
            fig.write_image(save_filename, width=width, height=height)
        except RuntimeError:
            import kaleido

            kaleido.get_chrome_sync()
        finally:
            fig.write_image(save_filename, width=width, height=height)
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
    if isinstance(in_data, dict):
        for key in in_data.keys():
            if isinstance(in_data[key], dict):
                x_ = in_data[key]["x"]
                y_ = in_data[key]["y"]
            elif isinstance(in_data[key], (tuple, list)):
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
    elif isinstance(in_data, (list, tuple)):
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


def section_profile_only_to_html_str(sec: Section) -> str:
    import plotly.io as pio

    from ada.api.curves import CurvePoly2d
    from ada.sections import SectionCat

    section_profile = sec.get_section_profile(True)

    def get_data(curve: CurvePoly2d):
        x = []
        y = []
        for edge in curve.points2d + [curve.points2d[0]]:
            x.append(edge[0])
            y.append(edge[1])
        return x, y

    xrange, yrange = None, None
    plot_data = dict()

    if section_profile.outer_curve is not None and not isinstance(section_profile.outer_curve, float):
        outer = get_data(section_profile.outer_curve)
        plot_data["outer"] = outer
        max_dim = max(max(outer[0]), max(outer[1]))
        min_dim = min(min(outer[0]), min(outer[1]))
        xrange = [min_dim, max_dim]
        yrange = [min_dim, max_dim]
    if section_profile.inner_curve is not None:
        inner = get_data(section_profile.inner_curve)
        plot_data["inner"] = inner

    # controls = []
    shapes = None
    if sec.type in SectionCat.circular:
        xrange = [-sec.r * 1.1, sec.r * 1.1]
        yrange = xrange
        shapes = [
            # unfilled circle
            dict(
                type="circle",
                xref="x",
                yref="y",
                x0=0,
                y0=0,
                x1=sec.r,
                y1=0,
                line_color="LightSeaGreen",
            )
        ]
    elif sec.type in SectionCat.tubular:
        xrange = [-sec.r * 1.1, sec.r * 1.1]
        yrange = xrange
        shapes = [
            dict(
                type="circle",
                xref="x",
                yref="y",
                x0=-sec.r,
                y0=-sec.r,
                x1=sec.r,
                y1=sec.r,
                line_color="LightSeaGreen",
            ),
            dict(
                type="circle",
                xref="x",
                yref="y",
                x0=-sec.r + sec.wt,
                y0=-sec.r + sec.wt,
                x1=sec.r - sec.wt,
                y1=sec.r - sec.wt,
                line_color="LightSeaGreen",
            ),
        ]

    fig = easy_plotly(
        f'ADA Section: "{sec.name}", Type: "{sec.type}"',
        plot_data,
        xrange=xrange,
        yrange=yrange,
        shapes=shapes,
        return_widget=True,
    )
    fig["layout"]["yaxis"]["scaleanchor"] = "x"

    plot_html = pio.to_html(fig, full_html=False, include_plotlyjs="cdn")

    return plot_html


def section_overview_to_html_str(sec: Section) -> str:
    """Builds a display for the section properties using Plotly and HTML (side-by-side layout)."""

    plot_html = section_profile_only_to_html_str(sec)

    html_content = "<b>Section Properties</b></br></br>"

    sp = sec.properties
    for sec_prop in [
        ("Ax", sp.Ax),
        ("Ix", sp.Ix),
        ("Iy", sp.Iy),
        ("Iz", sp.Iz),
        ("Iyz", sp.Iyz),
        ("Wxmin", sp.Wxmin),
        ("Wymin", sp.Wymin),
        ("Wzmin", sp.Wzmin),
        ("Sy", sp.Sy),
        ("Sz", sp.Sz),
        ("Shary", sp.Shary),
        ("Sharz", sp.Sharz),
        ("Shceny", sp.Shceny),
        ("Shcenz", sp.Shcenz),
    ]:
        res = sec_prop[1]
        if res is not None:
            html_content += f"<b>{sec_prop[0]}:</b> {sec_prop[1]:.4E}<br>"
        else:
            html_content += f"<b>{sec_prop[0]}:</b> Prop calc not defined yet<br>"

    # Wrap both elements inside a flexbox container
    final_html = f"""
<div style="display: flex; flex-direction: row; justify-content: space-between; align-items: flex-start;">
    <div style="flex: 1; padding-right: 20px;">{plot_html}</div>
    <div style="flex: 1; max-width: 400px;">{html_content}</div>
</div>
    """

    return final_html
