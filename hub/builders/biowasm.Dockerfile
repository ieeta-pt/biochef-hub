FROM emscripten/emsdk:2.0.25

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        autoconf \
        automake \
        autopoint \
        bison \
        build-essential \
        ca-certificates \
        cmake \
        curl \
        git \
        gettext \
        gperf \
        help2man \
        pkg-config \
        python3 \
        python3-pip \
        python3-venv \
        rsync \
        sudo \
        texinfo \
        wget \
        libtool \
        libtool-bin

WORKDIR /biowasm

RUN git clone https://github.com/biowasm/biowasm.git . && chmod -R 777 /biowasm

# biowasm compiles with -sENVIRONMENT=["web","worker"], and such a module
# refuses to start anywhere else, so hub test could never execute one. Adding
# node lets the test harness run these tools; it only adds a small amount of
# JS shim and changes nothing for the browser. The emscripten strategy sets the
# same value directly in its EM_FLAGS.
RUN sed -i 's|-s ENVIRONMENT=\["web","worker"\]|-s ENVIRONMENT=["web","worker","node"]|' bin/shared.sh \
    && grep -q '"node"' bin/shared.sh

RUN git config --system --add safe.directory '*'