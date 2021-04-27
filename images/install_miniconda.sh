#!/bin/sh

wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh &&
  mkdir /root/.conda &&
  bash Miniconda3-latest-Linux-x86_64.sh -b -p $(CONDAPATH) &&
  rm -f Miniconda3-latest-Linux-x86_64.sh &&
  echo "Running $(conda --version)" &&
  conda init bash &&
  . /root/.bashrc &&
  conda update conda &&
  conda env update -f environment.yml
