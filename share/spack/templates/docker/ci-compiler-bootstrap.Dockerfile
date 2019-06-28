# This Dockerfile should get generated a location such that the mirror
# directory containing the buildcache

FROM %%%BASE_IMAGE%%%

ARG COMPILER_TO_BOOTSTRAP=gcc@5.5.0
ARG BINARY_MIRROR_URL=https://mirror.spack.io

# Can we assume we already have spack bootstrapped?
RUN apt-get update && apt-get install -y --no-install-recommends             \
    build-essential                                                          \
    # other build tools
    ...                                                                   && \
    apt-get clean && rm -rf /var/lib/apt/lists/*                          && \
    # Now install the compiler
    spack mirror add local_mirror %%%BINARY_MIRROR_URL%%%                 && \
    spack install %%%COMPILER_TO_BOOTSTRAP%%%                             && \
    spack compiler find $(spack location -i %%%COMPILER_TO_BOOTSTRAP%%%)

