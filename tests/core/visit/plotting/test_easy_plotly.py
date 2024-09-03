from ada.visit.plots import easy_plotly


def test_easy_plotly_simple_graph_to_file(tmp_path):
    # If this tests runs indefinitely on win10/11 try downgrading to kaleido-core==0.1.0.
    plot_file_name = "MyEasyPlotlyPlot.png"
    easy_plotly(
        "MyPlot",
        [(0, 0), (1, 0.5), (1.5, 0.8), (2, 1.75)],
        "X label",
        "Y label",
        save_filename=tmp_path / plot_file_name,
    )
