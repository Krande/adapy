FROM krande/ada@sha256:3ba22bbbee0e8686cdf02b59e445014ae9a87e29aee1e1dd7031e7ba49bc2cf7

ARG TMPDIR=/tmp/adapy
ARG TESTDIR_FEM=/home/tests/fem
ARG TESTDIR=/home/tests/main
ARG TESTFILES=/home/tests/files

USER root
RUN rm -rfv /tmp/*
RUN mkdir ${TMPDIR}

WORKDIR ${TMPDIR}

RUN sudo apt-get -y update && sudo apt -y install git-all

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
RUN git clone --branch dev https://github.com/Krande/paradoc.git
RUN cd paradoc && pip install . --no-cache-dir

# Cleanup all temporary files from this and all previous steps
RUN rm -rfv /tmp/*
USER ${NB_USER}

WORKDIR ${HOME}

COPY examples examples

RUN chown -R ${NB_UID} examples