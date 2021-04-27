FROM ubuntu:20.04

ARG catemp=/tmp/code_aster

USER root
RUN mkdir ${catemp}
WORKDIR ${catemp}

RUN apt-get update && \
  apt-get upgrade -y --with-new-pkgs -o Dpkg::Options::="--force-confold" && \
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
    libboost-python-dev && \
  apt-get clean && \
  echo "C.UTF-8 UTF-8" >/etc/locale.gen && \
  locale-gen && \
  rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Set locale environment
ENV LC_ALL=C.UTF-8 \
    LANG=C.UTF-8 \
    LANGUAGE=C.UTF-8

# Variables
ENV ASTER_VER=14.6
ENV ASTER_FULL_SRC="https://code-aster.org/FICHIERS/aster-full-src-${ASTER_VER}.0-1.noarch.tar.gz"

# Download and install the latest stable version
RUN wget --no-check-certificate --quiet ${ASTER_FULL_SRC} -O aster_full.tar.gz && \
    mkdir aster_full && tar xf aster_full.tar.gz -C aster_full --strip-components 1

ENV TOOLS /tools
RUN mkdir ${TOOLS}
RUN cd aster_full && \
    python3 setup.py install --prefix=${TOOLS}/aster --noprompt

# Install Miniconda
ENV CONDAPATH ${TOOLS}/miniconda3

ENV PATH="${CONDAPATH}/bin:${PATH}"
ARG PATH="${CONDAPATH}/bin:${PATH}"

WORKDIR ${TOOLS}

COPY images/environment.yml .

RUN wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh && \
  mkdir /root/.conda && \
  bash Miniconda3-latest-Linux-x86_64.sh -b -p ${CONDAPATH} && \
  rm -f Miniconda3-latest-Linux-x86_64.sh && \
  echo "Running $(conda --version)" && \
  conda init bash && \
  . /root/.bashrc && \
  conda update conda && \
  conda env update -f environment.yml

# Add Tini. Tini operates as a process subreaper for jupyter. This prevents kernel crashes.
ENV TINI_VERSION v0.6.0
ADD https://github.com/krallin/tini/releases/download/${TINI_VERSION}/tini /usr/bin/tini
RUN chmod +x /usr/bin/tini
ENTRYPOINT ["/usr/bin/tini", "--"]

RUN mkdir /tmp/adapy
COPY . /tmp/adapy
RUN cd /tmp/adapy && pip install .

ENV ADA_code_aster_exe=${TOOLS}/aster/bin/as_run

LABEL code_aster="14.6"
LABEL python="3.8.8"

RUN rm -rf /tmp/aster_full.tar.gz /tmp/aster_full /tmp/adapy

CMD ["jupyter", "notebook", "--port=8888", "--no-browser", "--ip=0.0.0.0", "--allow-root"]