FROM krande/ada@sha256:e3fdddf0dbb3b87d7f0dfd8ce13267ebbf965e191a326446348a2ed600e7c1eb

ARG TMPDIR=/tmp/adapy
ARG TESTDIR_FEM=/home/tests/fem
ARG TESTDIR=/home/tests/main
ARG TESTFILES=/home/tests/files

USER root
RUN rm -rfv /tmp/*
RUN mkdir ${TMPDIR}

WORKDIR ${TMPDIR}

RUN conda install -c krande/label/dev -c krande -c conda-forge paradoc && conda clean -afy

#RUN apt-get -y update && apt -y install git-all
#RUN git clone https://github.com/Krande/paradoc.git
#RUN cd paradoc && pip install . --no-cache-dir

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

RUN adduser --disabled-password \
    --gecos "Default user" \
    --uid ${NB_UID} \
    ${NB_USER}

RUN mkdir "output"

RUN chown -R ${NB_UID} output
RUN chown -R ${NB_UID} examples

USER ${NB_USER}
WORKDIR ${HOME}

COPY examples examples
#EXPOSE 8888
#CMD ["/work/aster/mambaforge/bin/conda", "run", "-n", "adadocker", "/bin/bash", "-c", "jupyter notebook --port=8888 --no-browser --ip=0.0.0.0 --allow-root --NotebookApp.token=''"]