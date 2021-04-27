FROM krande/ada:code-aster

ARG nproc=2
ARG NB_USER=adauser
ARG NB_UID=1000
ENV USER ${NB_USER}
ENV NB_UID ${NB_UID}
ENV HOME /home/${NB_USER}

RUN apt-get update
RUN apt-get install ffmpeg libsm6 libxext6 libxft2 -y

# Installing necessary dependecies for Calculix
RUN apt-get -qq update && apt-get -qq install \
    build-essential \
    gfortran \
    curl \
    automake \
    make \
    autoconf \
    autotools-dev \
    bzip2  \
    sudo && \
    rm -rf /var/lib/apt/lists/*

ENV TOOLS /tools
WORKDIR ${TOOLS}

# Downloading CalculiX 2.16
RUN curl -s http://www.dhondt.de/ccx_2.16.src.tar.bz2 | tar -xj

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

WORKDIR ${TOOLS}/CalculiX/ccx_2.16/src

# replace hardcoded paths in CalculiX
RUN  sed -i -e 's|\.\./\.\./\.\./SPOOLES.2.2|${TOOLS}/spooles.2.2|g' \
            -e 's|\.\./\.\./\.\./ARPACK|/usr/local/ARPACK|g' \
            Makefile

RUN make -j $(nproc) --warn-undefined-variables
RUN cp ccx_2.16 /usr/local/bin
RUN chmod a+rx /usr/local/bin/ccx_2.16

ENV ADA_ccx_exe=/usr/local/bin/ccx_2.16