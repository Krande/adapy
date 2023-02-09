FROM krande/ada@sha256:c2adc79efb216cea84f1097e720f9789b2d13e36bdc4030c0ca5ffa3cb84e2cf

ARG TMPDIR=/tmp/adapy
ARG TESTDIR_FEM=/home/tests/fem
ARG TESTDIR=/home/tests/main
ARG TESTFILES=/home/tests/files

USER root

RUN mkdir ${TMPDIR}

WORKDIR ${TMPDIR}

RUN conda install -c krande/label/dev -c krande -c conda-forge paradoc && conda clean -afy

#RUN apt-get -y update && apt -y install git-all
#RUN git clone https://github.com/Krande/paradoc.git
#RUN cd paradoc && pip install . --no-cache-dir

COPY . .

RUN pip install . --no-cache-dir && rm -rfv ${TMPDIR}


RUN adduser --disabled-password \
    --gecos "Default user" \
    --uid ${NB_UID} \
    ${NB_USER}

WORKDIR ${HOME}
RUN mkdir "output"
COPY examples examples

RUN chown -R ${NB_UID} output
RUN chown -R ${NB_UID} examples

USER ${NB_USER}

#EXPOSE 8888
#CMD ["/work/aster/mambaforge/bin/conda", "run", "-n", "adadocker", "/bin/bash", "-c", "jupyter notebook --port=8888 --no-browser --ip=0.0.0.0 --allow-root --NotebookApp.token=''"]