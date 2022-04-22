cmd_pre=pip install pytest && conda list
cmd_test=cd /home/tests/fem && pytest && python build_verification_report.py
mount=--mount type=bind,source="$(CURDIR)/temp/report",target=/home/tests/fem/temp \
      --mount type=bind,source="$(CURDIR)/temp/scratch",target=/home/adauser/scratch
build_dirs=mkdir -p "temp/report" && mkdir -p "temp/scratch"
build_dirs_win=mkdir -p "temp/report" && mkdir -p "temp/scratch"

install:
	conda env create -f environment.yml

update:
	conda env update --name work --file environment.yml --prune

format:
	black . && isort . && flake8 .

bump:
	bumpversion patch setup.py

build:
	docker build -t ada/base:latest .

run:
	docker run --rm -p 8888:8888 ada/base:latest

test:
	cd tests && pytest --cov=ada --cov-report=xml --cov-report=html .

dtest:
	$(build_dirs) && \
	docker build -t ada/testing . && \
	docker run --name ada-report --rm $(mount) ada/testing bash -c "$(cmd_pre) && $(cmd_test)"

dtest-local:
	$(build_dirs_win) && \
	docker build -t ada/testing . && \
	docker run --name ada-report --rm $(mount) ada/testing bash -c "$(cmd_pre) && $(cmd_test)"

dtest-b:
	$(build_dirs) && docker build -t ada/testing .

dtest-r:
	docker run --name ada-report --rm $(mount) ada/testing bash -c "$(cmd_pre) && $(cmd_test)"
