# adapy version, exposed as ``ada.__version__``.
#
# pyproject.toml ``[project].version`` is the SINGLE SOURCE OF TRUTH (the CI release tooling
# bumps it). The placeholder below is replaced at build time by setup.py (BuildPyWithVersion)
# with that version, so an installed wheel/conda package reports the real version while
# ``ada.__version__`` stays a plain literal (no runtime metadata lookup). A source checkout that
# hasn't been built keeps this placeholder.
__version__ = "0.0.0.dev0"
