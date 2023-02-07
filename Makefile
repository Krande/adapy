mount=--mount type=bind,source="$(CURDIR)/temp/report",target=/home/tests/fem/temp \
      --mount type=bind,source="$(CURDIR)/temp/scratch",target=/home/adauser/scratch
drun=docker run --rm $(mount) krande/ada:femtests conda run --live-stream -n adadocker

mdir:
	mkdir "temp\report" && mkdir "temp\scratch"

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

bbase:
	docker build . -t krande/ada:base -f images/base.Dockerfile

bdev:
	docker build . -t krande/ada:dev -f images/dev.Dockerfile

bfem:
	docker build . -t krande/ada:femtests -f images/femtests.Dockerfile

dtest:
	$(drun) ./run_tests.sh

pbase:
	docker push krande/ada:base

run:
	docker run -it --rm -p 8888:8888 krande/adabase:latest

test:
	cd tests && pytest --cov=ada --cov-report=xml --cov-report=html .
