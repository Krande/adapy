FROM krande/ada@sha256:5343aa7e7ceba592f9d72d555ba0464d862bc8572bb686c19950f0a8e1e9bdb8

ARG TMPDIR=/tmp/adapy
ARG TESTDIR_FEM=/home/tests/fem
ARG TESTDIR=/home/tests/main
ARG TESTFILES=/home/tests/files

USER root
RUN rm -rfv /tmp/*
RUN mkdir ${TMPDIR}

WORKDIR ${TMPDIR}

COPY setup.cfg .
COPY pyproject.toml .
COPY MANIFEST.in .
COPY LICENSE .
COPY README.md .
COPY src src
COPY images/tests ${TESTDIR_FEM}
COPY tests ${TESTDIR}
COPY files ${TESTFILES}

RUN pip install . --no-cache-dir

# Cleanup all temporary files from this and all previous steps
RUN rm -rfv /tmp/*
USER ${NB_USER}

WORKDIR ${HOME}

COPY examples examples
