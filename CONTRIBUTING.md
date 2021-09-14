# Contributing

For developers interested in contributing to this project feel free to 
make a fork, experiment and create a pull request when you have something you 
would like to add/change/remove. 

Before making a pull request you need to lint with, isort, flake8 and black.
Assuming you have a cmd terminal open in the adapy package directory you can
run

````
pip install black isort flake8
isort .
flake8 .
black .
````

Or if you have make installed you can just run `make format` 
to run all three tools at once.
