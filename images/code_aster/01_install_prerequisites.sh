#!/bin/sh

apt-get update &&
  apt-get upgrade -y --with-new-pkgs -o Dpkg::Options::="--force-confold" &&
  apt-get install -y \
    locales sudo \
    gcc g++ gfortran \
    wget \
    python3 \
    python3-dev \
    python3-numpy \
    libxft2 \
    libxmu6 \
    libxss1 \
    patch \
    make cmake \
    grace \
    zlib1g-dev \
    tk bison flex \
    libglu1-mesa libxcursor-dev \
    libmpich-dev \
    libopenblas-dev \
    libsuperlu-dev \
    libboost-numpy-dev \
    libboost-python-dev &&
  apt-get clean &&
  echo "C.UTF-8 UTF-8" >/etc/locale.gen &&
  locale-gen &&
  rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*
