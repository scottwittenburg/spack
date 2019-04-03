#!/bin/bash

# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

###
### The following environment variables are expected to be set in order for
### the various elements in the underlying spack command to function properly.
### Listed first are two defaults we rely on from gitlab, then three we set up
### in the variables section of gitlab ourselves, and finally four variables
### written into the .gitlab-ci.yml file.
###
### CI_PROJECT_DIR (e.g. "/spack-ci/ci/spack")
### CI_JOB_NAME (e.g. "ncurses 6.1 clang@6.0.0 linux-centos7-x86_64 test-release-v1.4")
###
### AWS_ACCESS_KEY_ID
### AWS_SECRET_ACCESS_KEY
### SPACK_SIGNING_KEY
###
### CDASH_BASE_URL (e.g. "http://cdash")
### CDASH_PROJECT (e.g. "Spack Testing")
### DEPENDENCIES: pkgconf 1.5.4 gcc@5.5.0 linux-centos7-x86_64 test-release-v1.4
### MIRROR_URL: https://mirror.spack.io
### ROOT_SPEC: ncurses@6.1%gcc@5.5.0 arch=linux-centos7-x86_64
###

export SPACK_ROOT=${CI_PROJECT_DIR}
. "${SPACK_ROOT}/share/spack/setup-env.sh"

spack rebuild-package \
    --ci-project-dir "${CI_PROJECT_DIR}" \
    --ci-job-name "${CI_JOB_NAME}"       \
    --cdash-base-url "${CDASH_BASE_URL}" \
    --cdash-project "${CDASH_PROJECT}"   \
    --dependencies "${DEPENDENCIES}"     \
    --mirror-url "${MIRROR_URL}"         \
    --root-spec "${ROOT_SPEC}"
