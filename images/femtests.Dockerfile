FROM krande/ada:base@sha256:b2516a94fad091570f0c535fcdb1de748404e4e9fd8541824f3c96864ee339d1

SHELL ["/work/aster/mambaforge/bin/conda", "run", "-n", "adadocker", "/bin/bash", "-c"]

ARG TMPDIR=/tmp/adapy
RUN mkdir -p ${TMPDIR}
WORKDIR ${TMPDIR}
USER root

COPY . .

RUN pip install --no-cache-dir pytest && pip install --no-cache-dir . && rm -rfv ${TMPDIR}/*
ENV TESTDIR="${HOME}/tests/fem"

RUN mkdir -p "${TESTDIR}"

ENV ADA_FEM_DO_NOT_SAVE_CACHE=1
WORKDIR "${TESTDIR}"

COPY tests/dockertests/ .
RUN chown -R 1000:1000 ${TESTDIR}
RUN chmod -R 775 ${TESTDIR}

USER aster
