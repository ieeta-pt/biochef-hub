ARG EMSCRIPTEN_IMAGE
FROM ${EMSCRIPTEN_IMAGE}

ARG EMSCRIPTEN_IMAGE
ARG BIOWASM_REPOSITORY
ARG BIOWASM_COMMIT

LABEL dev.biochef.builder.base-image="${EMSCRIPTEN_IMAGE}" \
      dev.biochef.builder.biowasm-repository="${BIOWASM_REPOSITORY}" \
      dev.biochef.builder.biowasm-commit="${BIOWASM_COMMIT}"

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
        texinfo \
        wget \
        libtool \
        libtool-bin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /biowasm

RUN git init . \
    && git remote add origin "${BIOWASM_REPOSITORY}" \
    && git fetch --depth 1 origin "${BIOWASM_COMMIT}" \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "${BIOWASM_COMMIT}" \
    && groupadd --gid 10001 biochef \
    && useradd --uid 10001 --gid 10001 --create-home biochef \
    && chown -R biochef:biochef /biowasm

COPY biowasm_runner.py /usr/local/bin/biochef-biowasm-build
RUN chmod 0555 /usr/local/bin/biochef-biowasm-build

USER 10001:10001
