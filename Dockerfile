FROM krande/ada@sha256:18dac2a426b03dd67f6237f72375dbf2c061bd021fed462f6399935cbd56d902

ENV ADA_temp_dir ${HOME}/temp
ENV ADA_test_dir ${HOME}/temp/tests
ENV ADA_log_dir ${HOME}/temp/log
ENV ADA_scratch_dir ${HOME}/scratch

WORKDIR ${HOME}

COPY examples examples

RUN mkdir ${ADA_temp_dir} && mkdir ${ADA_scratch_dir} && mkdir ${ADA_test_dir} && mkdir ${ADA_log_dir}

USER root
RUN chown -R ${NB_UID} ${ADA_scratch_dir}
USER ${NB_USER}

COPY . /home/adapy
RUN cd /home/adapy && pip install .

