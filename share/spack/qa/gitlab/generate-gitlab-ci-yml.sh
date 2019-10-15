#! /usr/bin/env bash
#
# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

. "$(pwd)/share/spack/setup-env.sh"

# Gather variables used in dynamic job generation
GENERATE_ARGS=()

if [[ ! -z "${CDASH_AUTH_TOKEN}" ]]; then
    GENERATE_ARGS+=( "--cdash-token" "${CDASH_AUTH_TOKEN}" )
fi

# Generate the .gitlab-ci.yml dynamically
spack ci generate "${GENERATE_ARGS[@]}"

# Gather variables used to make a commit and push the result to CI repo
PUSHYAML_ARGS=()

if [[ ! -z "${DOWNSTREAM_CI_REPO}" ]]; then
    PUSHYAML_ARGS+=( "--downstream-repo" "${DOWNSTREAM_CI_REPO}" )
fi

if [[ ! -z "${CI_COMMIT_REF_NAME}" ]]; then
    PUSHYAML_ARGS+=( "--branch-name" "${CI_COMMIT_REF_NAME}" )
fi

if [[ ! -z "${CI_COMMIT_SHA}" ]]; then
    PUSHYAML_ARGS+=( "--commit-sha" "${CI_COMMIT_SHA}" )
fi

# Commit and push the generated file to the CI repo
spack ci pushyaml "${PUSHYAML_ARGS[@]}"
