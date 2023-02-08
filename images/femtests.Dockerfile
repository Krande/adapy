FROM krande/ada:base

SHELL ["/work/aster/mambaforge/bin/conda", "run", "-n", "adadocker", "/bin/bash", "-c"]

RUN pip install --no-cache-dir pytest

ENV TESTDIR="${HOME}/tests/fem"

RUN mkdir -p "${TESTDIR}"
WORKDIR "${TESTDIR}"

RUN echo "source activate adadocker" > ~/.bashrc
ENV PATH="/work/aster/mambaforge/bin:${PATH}"

COPY tests/dockertests .

RUN chmod +x run_tests.sh