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

RUN git config --system --add safe.directory '*'