FROM aethereng/codeaster-mpi
# Built locally from the source code -> https://github.com/aethereng/docker-codeaster

ENV PYTHONDONTWRITEBYTECODE=true

RUN curl -L -O "https://github.com/conda-forge/miniforge/releases/latest/download/Mambaforge-$(uname)-$(uname -m).sh" && \
  bash Mambaforge-$(uname)-$(uname -m).sh
COPY images/environment.yml .
RUN mamba env update -f environment.yml

# https://github.com/conda-forge/calculix-feedstock
# https://anaconda.org/conda-forge/calculix
RUN export ADA_ccx_exe=$(which ccx)
ENV ADA_ccx_exe=${ADA_ccx_exe}

# Make the image size smaller
# https://jcristharif.com/conda-docker-tips.html
RUN conda clean -afy
RUN find /opt/conda/ -follow -type f -name '*.a' -delete \
    && find /opt/conda/ -follow -type f -name '*.pyc' -delete \
    && find /opt/conda/ -follow -type f -name '*.js.map' -delete \