FROM krande/ada:base

SHELL ["/work/aster/mambaforge/bin/conda", "run", "-n", "adadocker", "/bin/bash", "-c"]

RUN pip install --no-cache-dir pytest

ENV TESTDIR="${HOME}/tests/fem"

RUN mkdir -p "${TESTDIR}"
WORKDIR "${TESTDIR}"

COPY tests/dockertests .