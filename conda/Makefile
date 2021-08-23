build:
	conda-build -c krande -c conda-forge . --keep-old-work

index:
	conda index ${CONDA_PREFIX}/conda-bld --channel-name local

install:
	conda create -n testenv -c local -c krande -c conda-forge ada-py --strict-channel-priority

test:
	cd ../tests && \
	pytest --doctest-modules --junitxml=junit/test-results.xml --cov=../src --cov-report=xml --cov-report=html

upload:
	conda-build -c krande -c conda-forge . --user krande

show:
	conda config --show channels