**Testing the pull request template**

**If relevant a pull request should include**
* A reference to a related issue in your repository.
* A description of the changes proposed in the pull request.
* @mentions of the person or team responsible for reviewing proposed changes.
* Make sure you have made a pull of the latest changes from the main branch and re-run all unittests
* Lastly remember to lint with, isort, flake8 and black 

To perform linting
````
pip install black isort flake8
cd src/ada
isort .
flake8 .
black .
````
