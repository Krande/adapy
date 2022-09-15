# Contributing

For developers interested in contributing to this project feel free to 
make a fork, experiment and create a pull request when you have something you 
would like to add/change/remove. 

Before making a pull request you need to lint with, isort, flake8 and black.

The recommended step for `adapy` is to add a pre-commit step for linting.

First install the `pre-commit` package using ```pip install pre-commit``` or ```conda install -c conda-forge pre-commit```

Then go to the `adapy` root directory and run `pre-commit install`.

Now linting is applied on every commit ensuring that only linted code is pushed. 