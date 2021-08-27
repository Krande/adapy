build:
	docker build -t ada/base:latest .

run:
	docker run --rm -p 8888:8888 ada/base:latest

format:
	black . && isort . && flake8 .

test:
	cd tests && pytest --doctest-modules --junitxml=junit/test-results.xml --cov=../src --cov-report=xml --cov-report=html