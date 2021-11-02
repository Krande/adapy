build:
	docker build -t ada/base:latest .

run:
	docker run --rm -p 8888:8888 ada/base:latest

format:
	black . && isort . && flake8 .

install:
	pip install .

test:
	cd tests && pytest --cov=ada --cov-report=xml --cov-report=html .



dtest:
	mkdir -p "temp" && \
	docker build -t ada/testing . && \
	docker run --name ada-report --rm --mount type=bind,source="$(CURDIR)/temp",target=/home/tests/fem/temp ada/testing bash -c "pip install pytest && conda list && cd /home/tests/fem && pytest && python build_verification_report.py"
