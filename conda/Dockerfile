FROM continuumio/miniconda3

# Create the environment:
COPY . .
RUN conda env create -f conda/environment.yml

# Make RUN commands use the new environment:
SHELL ["conda", "run", "-n", "myenv", "/bin/bash", "-c"]

RUN conda-build -c krande -c conda-forge . --keep-old-work --python 3.9.10