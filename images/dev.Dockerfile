FROM condaforge/mambaforge:22.11.1-4

RUN mamba create -y -n adadev -c krande/label/dev -c conda-forge ada-py python==3.11 && conda clean -afy

RUN mkdir "tmp/install"

WORKDIR "tmp/install"
COPY . .

RUN /opt/conda/envs/adadev/bin/pip install . --no-cache-dir
#RUN rm "/tmp/install"
