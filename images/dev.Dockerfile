FROM condaforge/mambaforge:22.9.0-2

RUN mamba create -y -n adadev -c conda-forge -c krande ada-py && conda clean -afy

RUN mkdir "tmp/install"

WORKDIR "tmp/install"
COPY . .

RUN /opt/conda/envs/adadev/bin/pip install .
