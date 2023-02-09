FROM krande/ada:base@sha256:c2adc79efb216cea84f1097e720f9789b2d13e36bdc4030c0ca5ffa3cb84e2cf

SHELL ["/work/aster/mambaforge/bin/conda", "run", "-n", "adadocker", "/bin/bash", "-c"]

ARG TMPDIR=/tmp/adapy
RUN mkdir -p ${TMPDIR}
WORKDIR ${TMPDIR}
USER root

COPY . .

RUN pip install --no-cache-dir pytest && pip install --no-cache-dir . && rm -rfv ${TMPDIR}/*
ENV TESTDIR="${HOME}/tests/fem"make

RUN mkdir -p "${TESTDIR}"

WORKDIR "${TESTDIR}"

COPY tests/dockertests/ .

RUN chmod +x run_tests.sh
USER aster