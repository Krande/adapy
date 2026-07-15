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