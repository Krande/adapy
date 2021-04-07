FROM krande/ada@sha256:760e0e0328d852f821573b220f32ff7fd7c6a56bde09b04ee78c6ab005bf82f8

COPY . /home/adapy
RUN cd /home/adapy && pip install .

