cmd_pre=conda activate adadocker && pip install pytest && conda list
cmd_test=conda activate adadocker &&cd /home/tests/fem && pytest && python build_verification_report.py
mount=--mount type=bind,source="$(CURDIR)/temp/report",target=/home/tests/fem/temp \
      --mount type=bind,source="$(CURDIR)/temp/scratch",target=/home/adauser/scratch
build_dirs=mkdir -p "temp/report" && mkdir -p "temp/scratch"
build_dirs_win=mkdir "temp\report" && mkdir "temp\scratch"
drun=docker run --live-stream --rm $(mount) krande/ada:femtests conda run -n adadocker

mdir:
	mkdir "temp\report" && mkdir "temp\scratch"

install:
	conda env create -f environment.yml

update:
	conda env update --file environment.yml --prune

dev:
	mamba env update --file environment.dev.yml --prune

format:
	black --config pyproject.toml . && isort . && ruff . --fix

bump:
	bumpversion patch setup.py

docs-install:
	conda env create -f docs/environment.docs.yml

docs-update:
	conda env update --file docs/environment.docs.yml --prune

docs-build:
	activate adadocs && cd docs && make html

build:
	docker build -t ada/base:latest .

run:
	docker run -it --rm -p 8888:8888 krande/adabase:latest

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
	docker run --name ada-report --rm $(mount) ada/base:latest bash -c '$(cmd_pre) && $(cmd_test)'

dtest-exec:
	$(drun) pytest . && python build_verification_report.py

bfem:
	docker build . -t krande/ada:femtests -f images/femtests.Dockerfile