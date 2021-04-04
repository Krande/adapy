FROM docker.pkg.github.com/krande/adapy/base:dev

COPY . /home/adapy
RUN cd /home/adapy && pip install .

