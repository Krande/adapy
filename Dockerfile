FROM krande/ada@sha256:e7f02a79d61fe9abf63efd1fb5865706e98b7dc017cffa9ee73b14b059c8cfd6

ARG TMPDIR=/tmp/adapy

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

RUN pip install . --no-cache-dir

# Cleanup all temporary files from this and all previous steps
RUN rm -rfv /tmp/*
USER ${NB_USER}

WORKDIR ${HOME}

COPY examples examples
