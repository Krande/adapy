import pathlib

from analyze import transient_modal_analysis
from plot import plot_sdof
from model import build_model


def main():
    scratch_dir = pathlib.Path('temp/sdof_test').resolve().absolute()
    scratch_dir.mkdir(exist_ok=True, parents=True)

    a = build_model("sdof_test")
    sql_file = transient_modal_analysis(a, scratch_dir)
    plot_sdof(sql_file, scratch_dir)


if __name__ == '__main__':
    main()
