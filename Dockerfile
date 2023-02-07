FROM krande/ada@sha256:e3fdddf0dbb3b87d7f0dfd8ce13267ebbf965e191a326446348a2ed600e7c1eb

ARG TMPDIR=/tmp/adapy
ARG TESTDIR_FEM=/home/tests/fem
ARG TESTDIR=/home/tests/main
ARG TESTFILES=/home/tests/files

USER root
RUN rm -rfv /tmp/*
RUN mkdir ${TMPDIR}

WORKDIR ${TMPDIR}

#RUN apt-get -y update && apt -y install git-all

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
RUN conda install -c krande/label/dev -c krande -c conda-forge paradoc && conda clean -afy
#RUN git clone https://github.com/Krande/paradoc.git
#RUN cd paradoc && pip install . --no-cache-dir

# Cleanup all temporary files from this and all previous steps
RUN rm -rfv /tmp/*
USER ${NB_USER}

WORKDIR ${HOME}

COPY examples examples
RUN mkdir "output"

RUN chown -R ${NB_UID} output
RUN chown -R ${NB_UID} examples