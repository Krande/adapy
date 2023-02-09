FROM krande/ada:base@sha256:c2adc79efb216cea84f1097e720f9789b2d13e36bdc4030c0ca5ffa3cb84e2cf

SHELL ["/work/aster/mambaforge/bin/conda", "run", "-n", "adadocker", "/bin/bash", "-c"]

RUN pip install --no-cache-dir pytest

ENV TESTDIR="${HOME}/tests/fem"

RUN mkdir -p "${TESTDIR}"
WORKDIR "${TESTDIR}"

RUN echo "source activate adadocker" > ~/.bashrc
ENV PATH="/work/aster/mambaforge/bin:${PATH}"

COPY tests/dockertests .

USER root
RUN chmod +x run_tests.sh
USER aster