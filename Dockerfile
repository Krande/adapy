FROM krande/ada@sha256:dc260984f5f329ca45ec21ae0c53c37662b2ddb197dd6a372e9725c64e3e6125

ARG TMPDIR=/tmp/adapy
ARG TESTDIR=/home/tests

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
COPY images/tests ${TESTDIR}

RUN pip install . --no-cache-dir

# Cleanup all temporary files from this and all previous steps
RUN rm -rfv /tmp/*
USER ${NB_USER}

WORKDIR ${HOME}

COPY examples examples
