from ada.visualize.plots import easy_plotly


def test_easy_plotly_simple_graph_to_file(test_dir):
    plot_file_name = "MyEasyPlotlyPlot.png"
    easy_plotly(
        "MyPlot",
        [(0, 0), (1, 0.5), (1.5, 0.8), (2, 1.75)],
        "X label",
        "Y label",
        save_filename=test_dir / plot_file_name,
    )
