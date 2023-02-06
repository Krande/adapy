FROM krande/ada@sha256:3bfe6db423064cd73f951f8e1d3257155d612cb3f18040f5a76fef0573435d9e

ARG TMPDIR=/tmp/adapy
ARG TESTDIR_FEM=/home/tests/fem
ARG TESTDIR=/home/tests/main
ARG TESTFILES=/home/tests/files

USER root
RUN rm -rfv /tmp/*
RUN mkdir ${TMPDIR}

WORKDIR ${TMPDIR}

RUN apt-get -y update && apt -y install git-all

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
RUN conda install -c krande -c conda-forge paradoc
RUN git clone https://github.com/Krande/paradoc.git
RUN cd paradoc && pip install . --no-cache-dir

# Cleanup all temporary files from this and all previous steps
RUN rm -rfv /tmp/*
USER ${NB_USER}

WORKDIR ${HOME}

COPY examples examples
RUN mkdir "output"

RUN chown -R ${NB_UID} output
RUN chown -R ${NB_UID} examples