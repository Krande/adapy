mount=--mount type=bind,source="$(CURDIR)/temp/report",target=/aster/work/tests/fem/temp \
      --mount type=bind,source="$(CURDIR)/temp/scratch",target=/aster/work/scratch
drun=docker run --user aster --rm $(mount) krande/ada:femtests conda run --live-stream -n adadocker


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

mdir:
	mkdir -p temp/report && mkdir temp/scratch

dtest:
	$(drun) ./run_tests.sh

dprint:
	docker run --rm $(mount) krande/ada:femtests ls

pbase:
	docker push krande/ada:base

run:
	docker run -it --rm -p 8888:8888 krande/adabase:latest

test:
	cd tests && pytest --cov=ada --cov-report=xml --cov-report=html .
