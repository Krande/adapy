FROM krande/codeaster:mpi-15.5.2
# Built locally from the source code -> https://github.com/aethereng/docker-codeaster

ENV PYTHONDONTWRITEBYTECODE=true
ENV PYTHONUNBUFFERED 1
ENV DEBIAN_FRONTEND=noninteractive

RUN wget "https://github.com/conda-forge/miniforge/releases/latest/download/Mambaforge-$(uname)-$(uname -m).sh" && \
  bash Mambaforge-$(uname)-$(uname -m).sh -b && \
    rm Mambaforge-$(uname)-$(uname -m).sh

SHELL ["/work/aster/mambaforge/bin/conda", "run", "/bin/bash", "-c"]

COPY images/environment.yml .

# Make the image size smaller
# https://jcristharif.com/conda-docker-tips.html

RUN mamba env update -f environment.yml && conda clean -afy


#RUN find '/work/aster/mambaforge/' -follow -type f -name '*.a' -delete \
#    && find '/work/aster/mambaforge/' -follow -type f -name '*.pyc' -delete \
#    && find '/work/aster/mambaforge/' -follow -type f -name '*.js.map' -delete \

SHELL ["/work/aster/mambaforge/bin/conda", "run", "-n", "adadocker", "/bin/bash", "-c"]

ENV ADA_code_aster_exe=/aster/asrun/bin/as_run
ENV ADA_ccx_exe=/work/aster/mambaforge/envs/adadocker/bin/ccx
# https://github.com/conda-forge/calculix-feedstock
# https://anaconda.org/conda-forge/calculix

USER root

ARG NB_UID=1000
ENV NB_UID ${NB_UID}
ARG NB_USER=aster
ENV USER ${NB_USER}
ENV HOME /${NB_USER}/work
RUN mkdir -p ${HOME}

ENV ADA_temp_dir ${HOME}/temp
ENV ADA_test_dir ${HOME}/temp/tests
ENV ADA_log_dir ${HOME}/temp/log
ENV ADA_scratch_dir ${HOME}/scratch

USER root

RUN rm -rfv /tmp/*
RUN chown -R ${NB_UID} ${HOME}

RUN apt-get autoremove -y && apt-get clean -y && rm -rf /var/lib/apt/lists/*

USER ${NB_USER}

WORKDIR ${HOME}
COPY examples examples

RUN echo "source activate adadocker" > ~/.bashrc
ENV PATH="/work/aster/mambaforge/bin:${PATH}"