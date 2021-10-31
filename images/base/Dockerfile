FROM ubuntu:20.04 as base

ENV TOOLS /tools
RUN apt-get update && \
    apt-get upgrade -y --with-new-pkgs -o Dpkg::Options::="--force-confold"

#region install Calculix
FROM base AS calculix
RUN apt-get install ffmpeg libsm6 libxext6 libxft2 -y
ENV CCXVER "2.17"
# Installing necessary dependecies for Calculix
RUN apt-get install -y \
    build-essential \
    gfortran \
    curl \
    automake \
    make \
    autoconf \
    autotools-dev \
    bzip2  \
    sudo

WORKDIR ${TOOLS}

# Downloading CalculiX
RUN curl -s http://www.dhondt.de/ccx_${CCXVER}.src.tar.bz2 | tar -xj

# Installing spooles
WORKDIR ${TOOLS}
RUN mkdir spooles.2.2 && cd spooles.2.2 && \
    curl -s http://www.netlib.org/linalg/spooles/spooles.2.2.tgz | tar -xz && \
    cd ${TOOLS}/spooles.2.2/Tree/src/ && \
    sed -i 's/drawTree/draw/g' makeGlobalLib && \
    cd ${TOOLS}/spooles.2.2/ && \
    sed -i "s#CC = /usr/lang-4.0/bin/cc#CC = /usr/bin/cc#g" Make.inc && \
    make lib && cd ${TOOLS}/spooles.2.2/MT/src/ && make

# Downloading and installing ARPACK
WORKDIR ${TOOLS}
RUN curl -s https://www.caam.rice.edu//software/ARPACK/SRC/arpack96.tar.gz | tar -xz && \
    mv ARPACK /usr/local/ARPACK \
    && cd /usr/local/ARPACK \
    && sed -i 's/$(HOME)/\/usr\/local/g' ARmake.inc \
    && sed -i 's/\/bin\/make/make/g' ARmake.inc \
    && sed -i 's/f77/gfortran/g' ARmake.inc \
    && sed -i 's/SUN4/INTEL/g' ARmake.inc \
    && sed -i 's/-cg89//g' ARmake.inc \
    && sed -i 's/      EXTERNAL           ETIME/*     EXTERNAL           ETIME/g' UTIL/second.f \
    && make all

WORKDIR ${TOOLS}/CalculiX/ccx_${CCXVER}/src

# replace hardcoded paths in CalculiX
RUN  sed -i -e 's|\.\./\.\./\.\./SPOOLES.2.2|${TOOLS}/spooles.2.2|g' \
            -e 's|\.\./\.\./\.\./ARPACK|/usr/local/ARPACK|g' \
            Makefile

ENV nproc=2
RUN make -j $(nproc) --warn-undefined-variables
RUN cp ccx_${CCXVER} /usr/local/bin
RUN chmod a+rx /usr/local/bin/ccx_${CCXVER}

LABEL calculix=${CCXVER}
ENV ADA_ccx_exe=/usr/local/bin/ccx_${CCXVER}
#endregion

#region Code Aster
FROM calculix AS code_aster
ARG catemp=/tmp/code_aster

USER root
RUN mkdir ${catemp}
WORKDIR ${catemp}

RUN apt-get install -y \
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

RUN cd aster_full && \
    python3 setup.py install --prefix=${TOOLS}/aster --noprompt

LABEL code_aster="${ASTER_VER}"
ENV ADA_code_aster_exe=${TOOLS}/aster/bin/as_run
#endregion

#region Install Miniconda
FROM code_aster AS miniconda
ENV CONDAPATH ${TOOLS}/miniconda3
ENV CONDABLD /tmp/condabuild

ENV PATH="${CONDAPATH}/bin:${PATH}"
ARG PATH="${CONDAPATH}/bin:${PATH}"

RUN mkdir ${CONDABLD}
WORKDIR ${CONDABLD}


RUN wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh && \
  mkdir /root/.conda && \
  bash Miniconda3-latest-Linux-x86_64.sh -b -p ${CONDAPATH} && \
  rm -f Miniconda3-latest-Linux-x86_64.sh && \
  echo "Running $(conda --version)" && \
  conda init bash && \
  . /root/.bashrc


COPY images/environment.yml .

RUN conda update conda && conda env update -f environment.yml

#endregion

#region Install and Setup ADA and Jupyter Client

# Add Tini. Tini operates as a process subreaper for jupyter. This prevents kernel crashes.
ENV TINI_VERSION v0.19.0
ADD https://github.com/krallin/tini/releases/download/${TINI_VERSION}/tini /usr/bin/tini
RUN chmod +x /usr/bin/tini
ENTRYPOINT ["/usr/bin/tini", "--"]

RUN conda clean -a

LABEL python="3.9"

CMD ["jupyter", "notebook", "--port=8888", "--no-browser", "--ip=0.0.0.0", "--allow-root"]

#endregion

#region Binder Run Settings
ARG NB_UID=1000
ENV NB_UID ${NB_UID}
ARG NB_USER=adauser
ENV USER ${NB_USER}
ENV HOME /home/${NB_USER}

ENV ADA_temp_dir ${HOME}/temp
ENV ADA_test_dir ${HOME}/temp/tests
ENV ADA_log_dir ${HOME}/temp/log
ENV ADA_scratch_dir ${HOME}/scratch

RUN mkdir -p ${HOME} && mkdir ${ADA_temp_dir} && mkdir ${ADA_scratch_dir} && mkdir ${ADA_test_dir} && mkdir ${ADA_log_dir}

RUN adduser --disabled-password --gecos "Default user" --uid ${NB_UID} ${NB_USER}
USER root
RUN rm -rfv /tmp/*
RUN chown -R ${NB_UID} ${HOME}
USER ${NB_USER}

WORKDIR ${HOME}
#COPY examples examples
#endregion
