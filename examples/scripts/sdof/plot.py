import pathlib

import pandas as pd
import plotly.graph_objects as go

from ada.fem.results.sqlite_store import SQLiteFEAStore


def plot_sdof(sqlite_file, plot_dir):
    if isinstance(plot_dir, str):
        plot_dir = pathlib.Path(plot_dir)

    title = "SDOF: Displacement vs. Time"
    xaxis_title = "Time [s]"
    yaxis_title = "Displacement [m]"

    sql_store = SQLiteFEAStore(sqlite_file)
    steps = sql_store.get_steps()
    if len(steps) == 0:
        raise ValueError("It appears that there is no step data")
    columns = ["Name", "Restype", "PointID", "StepName", "FieldVarName", "Frame", "Value"]
    fig = go.Figure()
    for step_id, step_name, step_descr, step_domain_type in steps:
        # Plot displacement
        legend = f"{step_name}_{step_descr}_U1"
        df = pd.DataFrame(sql_store.get_history_data("U1", step_id), columns=columns)
        fig.add_trace(go.Scatter(x=df["Frame"], y=df["Value"], mode="lines", name=legend))

        # Plot speed
        legend = f"{step_name}_{step_descr}_V1"
        df = pd.DataFrame(sql_store.get_history_data("V1", step_id), columns=columns)
        fig.add_trace(go.Scatter(x=df["Frame"], y=df["Value"], mode="lines", name=legend))

        # Plot acceleration
        legend = f"{step_name}_{step_descr}_A1"
        df = pd.DataFrame(sql_store.get_history_data("A1", step_id), columns=columns)
        fig.add_trace(go.Scatter(x=df["Frame"], y=df["Value"], mode="lines", name=legend))

    fig.update_layout(
        title=title,
        xaxis_title=xaxis_title,
        yaxis_title=yaxis_title,
        # font=dict(family="Courier New, monospace", size=18, color="#7f7f7f"),
    )
    fig.write_html(plot_dir / "results.html")
    # fig.show()


if __name__ == "__main__":
    plot_sdof("temp")
