# Contributing

For developers interested in contributing to this project feel free to 
make a fork, experiment and create a pull request when you have something you 
would like to add/change/remove. 

Before making a pull request you need to lint with, isort, ruff and black.

If you have pixi installed there is now experimental support.

To lint with pixi do this: `pixi run lint`

That rewrites your files in place. To see what CI will say without changing anything, run
`pixi run lint-check` — same tools and config in check mode. It is the exact command the lint job
runs, so a green `lint-check` locally means a green lint job.

if pixi is not installed, you can find the isort, black and ruff versions in the `pyproject.toml` file,
and install them yourself and use the tools however you like.

## Environment variables

- New variables use the `ADA_` prefix. Existing `ADAPY_` names stay.
- A setting that belongs in `ada_config.toml` is a `Config` entry. A switch that must take effect at call time is a
  direct `os.environ` read where it is used, because `Config` is created during `import ada` and only sees later changes
  after a reload.
- A direct read must not use a name of the form `ADA_<SECTION>_<KEY>` that matches a `Config` entry, since `Config`
  reads those too. `tests/core/config/test_environment_variable_names.py` checks this.
- A rename keeps reading the old name as a fallback, newest name first.
