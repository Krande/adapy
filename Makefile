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

dtest_tot:
	docker build -t ada/testing . && \
	docker run --mount type=bind,source="$(pwd)/tmp",target=/home/tests/fem/temp ada/testing bash -c "pip install pytest pandas pydantic && cd /home/tests/fem && pytest && python build_verification_report.py"

dtest_fem:
	docker build -t ada/testing . && \
	docker run ada/testing bash -c "pip install pytest && cd /home/tests/fem && pytest"

dtest:
	docker build -t ada/testing . && \
	docker run ada/testing bash -c "pip install pytest && cd /home/tests/main && pytest"