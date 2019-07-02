ARG BASE_BUILDER_IMAGE=spack/ubuntu:18.04

FROM ${BASE_BUILDER_IMAGE}

ARG BINARY_MIRROR_URL=https://mirror.spack.io
ARG COMPILER_TO_BOOTSTRAP=gcc@5.5.0

RUN spack mirror add my_mirror ${BINARY_MIRROR_URL}                    && \
    spack install --use-cache ${COMPILER_TO_BOOTSTRAP}                 && \
    spack compiler find $(spack location -i ${COMPILER_TO_BOOTSTRAP})
