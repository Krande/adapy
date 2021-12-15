cmd_pre=pip install pytest && conda list
cmd_test=cd /home/tests/fem && pytest && python build_verification_report.py
mount=--mount type=bind,source="$(CURDIR)/temp/report",target=/home/tests/fem/temp \
      --mount type=bind,source="$(CURDIR)/temp/scratch",target=/home/adauser/scratch
build_dirs=mkdir "temp/report" && mkdir "temp/scratch"


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
	$(build_dirs) && \
	docker build -t ada/testing . && \
	docker run --name ada-report --rm $(mount) ada/testing bash -c "$(cmd_pre) && $(cmd_test)"

dtest-b:
	$(build_dirs) && docker build -t ada/testing .

dtest-r:
	docker run --name ada-report --rm $(mount) ada/testing bash -c "$(cmd_pre) && $(cmd_test)"
