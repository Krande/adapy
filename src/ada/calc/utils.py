def equation_compiler(f, print_latex=False, print_formula=False):
    from inspect import getsourcelines

    try:
        import pytexit
    except ModuleNotFoundError as e:
        raise ModuleNotFoundError(
            "To use the equation compiler you will need to install pytexit first.\n"
            'Use "pip install pytexit"\n\n'
            f'Original error message: "{e}"'
        )

    lines = getsourcelines(f)
    final_line = lines[0][-1]
    return pytexit.py2tex(final_line.replace("return ", ""), print_latex=print_latex, print_formula=print_formula)
