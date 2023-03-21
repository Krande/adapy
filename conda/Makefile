compile:
	conda run -n conda-build --live-stream conda mambabuild -c conda-forge -c krande . --python 3.11

compile-core:
	conda run -n conda-build --live-stream conda mambabuild -c krande/label/dev -c conda-forge . --variants "{'variant': 'core'}" --python 3.11


devcompile:
	conda run -n conda-build --live-stream conda mambabuild -c krande/label/dev -c conda-forge . --python 3.11

compile-docker-build:
	docker build -t ada/condabuild -f Dockerfile ../ && \
	docker run --name ada-condabuild --rm ada/condabuild

compile-docker:
	docker exec -it ada-condabuild "conda-build -c krande/label/dev -c conda-forge . --keep-old-work --python 3.11"


install:
	conda create -n testenv -c local -c krande -c conda-forge ada-py --strict-channel-priority

test:
	cd ../tests && \
	pytest --doctest-modules --junitxml=junit/test-results.xml --cov=../src --cov-report=xml --cov-report=html

upload:
	conda-build -c krande -c conda-forge . --user krande

show:
	conda config --show channels

pre:
	mamba env update --file environment.build.yml --prune