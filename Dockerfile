FROM krande/ada@sha256:39c95ff64e455be6f083501ea8aaf6f0066d401da9071fab9f6058e32a82a0d7

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
