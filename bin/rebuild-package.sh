#!/bin/bash

# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

# ci_project_dir = get_env_var('CI_PROJECT_DIR')
# signing_key = get_env_var('SPACK_SIGNING_KEY')
# enable_cdash = get_env_var('SPACK_ENABLE_CDASH')
# root_spec = get_env_var('SPACK_ROOT_SPEC')
# remote_mirror_url = get_env_var('SPACK_MIRROR_URL')
# enable_artifacts_mirror = get_env_var('SPACK_ENABLE_ARTIFACTS_MIRROR')
# job_spec_pkg_name = get_env_var('SPACK_JOB_SPEC_PKG_NAME')
# compiler_action = get_env_var('SPACK_COMPILER_ACTION')
# cdash_base_url = get_env_var('SPACK_CDASH_BASE_URL')
# cdash_project = get_env_var('SPACK_CDASH_PROJECT')
# cdash_project_enc = get_env_var('SPACK_CDASH_PROJECT_ENC')
# cdash_build_name = get_env_var('SPACK_CDASH_BUILD_NAME')
# cdash_site = get_env_var('SPACK_CDASH_SITE')
# related_builds = get_env_var('SPACK_RELATED_BUILDS')
# job_spec_buildgroup = get_env_var('SPACK_JOB_SPEC_BUILDGROUP')

export SPACK_ROOT="${CI_PROJECT_DIR}"
. "${SPACK_ROOT}/share/spack/setup-env.sh"

# Gather variables used by the rebuild pkg command
CLI_ARGS=()

if [[ ! -z "${CI_PROJECT_DIR}" ]]; then
    CLI_ARGS+=( "--ci-artifact-dir" "${CI_PROJECT_DIR}" )
fi

if [[ ! -z "${SPACK_SIGNING_KEY}" ]]; then
    CLI_ARGS+=( "--signing-key" "${SPACK_SIGNING_KEY}" )
fi

# Execute the command
spack rebuild-package "${CLI_ARGS[@]}"

echo "That's all folks!"
