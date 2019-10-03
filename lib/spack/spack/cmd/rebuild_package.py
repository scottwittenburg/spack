# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import argparse
from base64 import b64decode
import os
import shutil
import sys
from subprocess import Popen, PIPE
import tempfile

from six import iteritems
from six.moves.urllib.parse import urlencode

import llnl.util.tty as tty

import spack.binary_distribution as bindist
import spack.config as cfg
from spack.main import SpackCommand
from spack.spec import Spec, save_dependency_spec_yamls
import spack.util.spack_yaml as syaml

description = "build (as necessary) a package in the release workflow"
section = "build"
level = "long"


spack_gpg = SpackCommand('gpg')
spack_compiler = SpackCommand('compiler')
# spack_buildcache = SpackCommand('buildcache')
spack_config = SpackCommand('config')
spack_mirror = SpackCommand('mirror')
spack_install = SpackCommand('install')


def setup_parser(subparser):
    pass


def get_env_var(variable_name):
    if variable_name in os.environ:
        return os.environ[variable_name]
    return None


def url_encode_string(input_string):
    encoded_keyval = urlencode({'donotcare': input_string})
    eq_idx = encoded_keyval.find('=') + 1
    encoded_value = encoded_keyval[eq_idx:]


def save_full_yamls(job_name, dependencies, root_spec_name, output_dir):
    parts_list = job_name.split()

    pkg_name = parts_list[0]
    pkg_version = parts_list[1]
    compiler = parts_list[2]
    os_arch = parts_list[3]
    release_tag = parts_list[4]

    job_spec_name = '{0}@{1}%{2} arch={3} ({4})'.format(
        pkg_name, pkg_version, compiler, os_arch, release_tag)
    job_group = release_tag
    spec_yaml_path = os.path.join(output_dir, '{0}.yaml'.format(pkg_name))

    job_deps_pkg_names = []

    deps_list = dependencies.split(';')
    for dep_job_name in deps_list:
        dep_parts_list = dep_job_name.split()
        dep_pkg_name = dep_parts_list[0]
        job_deps_pkg_names.append(dep_pkg_name)

    root_spec = Spec(args.root_spec)
    root_spec.concretize()
    root_spec_as_yaml = root_spec.to_yaml(all_deps=True)

    save_dependency_spec_yamls(
        root_spec_as_yaml, output_dir, job_deps_pkg_names + [pkg_name])

    return job_spec_name, job_group, spec_yaml_path


def perform_full_rebuild(spec_yaml_path, job_spec_name, cdash_upload_url,
    local_mirror_dir):
    # Configure mirror
    spack_mirror('add', 'local_artifact_mirror', 'file://{0}'.format(
        local_mirror_dir))

    job_cdash_id = "NONE"

    # Install package, using the buildcache from the local mirror to
    # satisfy dependencies.
    spack_install('install', '--keep-stage',
        '--cdash-upload-url', cdash_upload_url,
        '--cdash-build', job_spec_name,
        '--cdash-site', 'Spack AWS Gitlab Instance'
        '--cdash-track', job_group,
        '-f', spec_yaml_path)

    """
    # Copy some log files into an artifact location
    stage_dir=$(spack location --stage-dir -f "${SPEC_YAML_PATH}")
    build_log_file=$(find -L "${stage_dir}" | grep "spack-build\\.out")
    config_log_file=$(find -L "${stage_dir}" | grep "config\\.log")
    cp "${build_log_file}" "${JOB_LOG_DIR}/"
    cp "${config_log_file}" "${JOB_LOG_DIR}/"

    # By parsing the output of the "spack install" command, we can get the
    # buildid generated for us by CDash
    JOB_CDASH_ID=$(extract_build_id "${BUILD_ID_LINE}")

    # Create buildcache entry for this package, reading the spec from the yaml
    # file.
    spack -d buildcache create --spec-yaml "${SPEC_YAML_PATH}" -a -f -d "${LOCAL_MIRROR}" --no-rebuild-index
    check_error $? "spack buildcache create"

    # Write the .cdashid file to the buildcache as well
    echo "${JOB_CDASH_ID}" >> ${JOB_CDASH_ID_FILE}

    # TODO: The upload-s3 command should eventually be replaced with something
    # like: "spack buildcache put <mirror> <spec>", when that subcommand is
    # properly implemented.
    spack -d upload-s3 spec --base-dir "${LOCAL_MIRROR}" --spec-yaml "${SPEC_YAML_PATH}"
    check_error $? "spack upload-s3 spec"
    """


def download_buildcache(spec_yaml_path, job_spec_name, build_cache_dir,
    remote_mirror_url):
    tty.msg('{0} is up to date on {1}, downloading it'.format(
        job_spec_name, remote_mirror_url))

    # Configure remote mirror so we can download buildcache entry
    spack_mirror('add', 'remote_binary_mirror', remote_mirror_url)

    # Now download it
    spack_buildcache('download', '--spec-yaml', spec_yaml_path,
                     '--path', build_cache_dir, '--require-cdashid')




def relate_build_to_dependencies():
    pass
    """
    if [ -f "${JOB_CDASH_ID_FILE}" ]; then
        JOB_CDASH_BUILD_ID=$(<${JOB_CDASH_ID_FILE})

        if [ "${JOB_CDASH_BUILD_ID}" == "NONE" ]; then
            echo "ERROR: unable to read this jobs id from ${JOB_CDASH_ID_FILE}"
            exit 1
        fi

        # Now get CDash ids for dependencies and "relate" each dependency build
        # with this jobs build
        for DEP_PKG_NAME in "${JOB_DEPS_PKG_NAMES[@]}"; do
            echo "Getting cdash id for dependency --> ${DEP_PKG_NAME} <--"
            DEP_SPEC_YAML_PATH="${SPEC_DIR}/${DEP_PKG_NAME}.yaml"
            DEP_JOB_BUILDCACHE_NAME=`spack -d buildcache get-buildcache-name --spec-yaml "${DEP_SPEC_YAML_PATH}"`

            if [[ $? -eq 0 ]]; then
                DEP_JOB_ID_FILE="${BUILD_CACHE_DIR}/${DEP_JOB_BUILDCACHE_NAME}.cdashid"
                echo "DEP_JOB_ID_FILE path = ${DEP_JOB_ID_FILE}"

                if [ -f "${DEP_JOB_ID_FILE}" ]; then
                    DEP_JOB_CDASH_BUILD_ID=$(<${DEP_JOB_ID_FILE})
                    echo "File ${DEP_JOB_ID_FILE} contained value ${DEP_JOB_CDASH_BUILD_ID}"
                    echo "Relating builds -> ${JOB_SPEC_NAME} (buildid=${JOB_CDASH_BUILD_ID}) depends on ${DEP_PKG_NAME} (buildid=${DEP_JOB_CDASH_BUILD_ID})"
                    relateBuildsPostBody="$(get_relate_builds_post_data "${CDASH_PROJECT}" ${JOB_CDASH_BUILD_ID} ${DEP_JOB_CDASH_BUILD_ID})"
                    relateBuildsResult=`curl "${DEP_JOB_RELATEBUILDS_URL}" -H "Content-Type: application/json" -H "Accept: application/json" -d "${relateBuildsPostBody}"`
                    echo "Result of curl request: ${relateBuildsResult}"
                else
                    echo "ERROR: Did not find expected .cdashid file for dependency: ${DEP_JOB_ID_FILE}"
                    exit 1
                fi
            else
                echo "ERROR: Unable to get buildcache entry name for ${DEP_SPEC_NAME}"
                exit 1
            fi
        done
    else
        echo "ERROR: Did not find expected .cdashid file ${JOB_CDASH_ID_FILE}"
        exit 1
    fi
    """


def import_signing_key(base64_signing_key):
    if not base64_signing_key:
        tty.die('No key found for signing/verifying packages')

    tty.msg('hello from import_signing_key')

    # This command has the side-effect of creating the directory referred
    # to as GNUPGHOME in setup_environment()
    list_output = spack_gpg('list', output=str)

    tty.msg('spack gpg list:')
    tty.msg(list_output)

    # Importing the secret key using gpg2 directly should allow both
    # signing and verification
    gpg_process = Popen(["gpg2", "--import"], stdin=PIPE)
    decoded_key = b64decode(base64_signing_key)
    gpg_out, gpg_err = gpg_process.communicate(decoded_key)

    if gpg_out:
        tty.msg('gpg2 output: {0}'.format(gpg_out))

    if gpg_err:
        tty.msg('gpg2 error: {0}'.format(gpg_err))

    # Now print the keys we have for verifying and signing
    trusted_keys_output = spack_gpg('list', '--trusted', output=str)
    signing_keys_output = spack_gpg('list', '--signing' ,output=str)

    tty.msg('spack list --trusted')
    tty.msg(trusted_keys_output)
    tty.msg('spack list --signing')
    tty.msg(signing_keys_output)


def configure_compilers(compiler_action):
    if compiler_action == 'INSTALL_MISSING':
        tty.msg('Make sure bootstrapped compiler will be installed')
        config = cfg.get('config')
        config['install_missing_compilers'] = True
        cfg.set('config', config)
    elif compiler_action == 'FIND_ANY':
        tty.msg('Just find any available compiler')
        output = spack_compiler('find')
        tty.msg('spack compiler find')
        tty.msg(output)
    else:
        tty.msg('No compiler action to be taken')


def rebuild_package(parser, args):
    """ This command represents a gitlab-ci job, corresponding to a single
    release spec.  As such it must first decide whether or not the spec it
    has been assigned to build (represented by args.ci_job_name) is up to
    date on the remote binary mirror.  If it is not (i.e. the full_hash of
    the spec as computed locally does not match the one stored in the
    metadata on the mirror), this script will build the package, create a
    binary cache for it, and then push all related files to the remote binary
    mirror.  This script also communicates with a remote CDash instance to
    share status on the package build process. """

    ci_project_dir = get_env_var('CI_PROJECT_DIR')
    ci_job_name = get_env_var('CI_JOB_NAME')
    spack_signing_key = get_env_var('SPACK_SIGNING_KEY')
    spack_enable_cdash = get_env_var('SPACK_ENABLE_CDASH')
    spack_root_spec = get_env_var('SPACK_ROOT_SPEC')
    spack_mirror_url = get_env_var('SPACK_MIRROR_URL')
    spack_job_spec_pkg_name = get_env_var('SPACK_JOB_SPEC_PKG_NAME')
    spack_compiler_action = get_env_var('SPACK_COMPILER_ACTION')
    spack_cdash_base_url = get_env_var('SPACK_CDASH_BASE_URL')
    spack_cdash_project = get_env_var('SPACK_CDASH_PROJECT')
    spack_cdash_project_enc = get_env_var('SPACK_CDASH_PROJECT_ENC')
    spack_cdash_build_name = get_env_var('SPACK_CDASH_BUILD_NAME')
    spack_cdash_site = get_env_var('SPACK_CDASH_SITE')
    spack_related_builds = get_env_var('SPACK_RELATED_BUILDS')
    spack_job_spec_buildgroup = get_env_var('SPACK_JOB_SPEC_BUILDGROUP')

    os.environ['FORCE_UNSAFE_CONFIGURE'] = '1'
    os.environ['GNUPGHOME'] = '{0}/opt/spack/gpg'.format(ci_project_dir)

    # The following environment variables should have been provided by the CI
    # infrastructre (or some other external source).  The AWS keys are
    # used to upload buildcache entries to S3 using the boto3 api.  We import
    # the SPACK_SIGNING_KEY using the "gpg2 --import" command, it is used both
    # for verifying dependency buildcache entries and signing the buildcache
    # entry we create for our target pkg.
    #
    # AWS_ACCESS_KEY_ID
    # AWS_SECRET_ACCESS_KEY
    # AWS_ENDPOINT_URL (only needed for non-AWS S3 implementations)
    # SPACK_SIGNING_KEY

    temp_dir = os.path.join(ci_project_dir, 'jobs_scratch_dir')
    job_log_dir = os.path.join(temp_dir, 'logs')
    spec_dir = os.path.join(temp_dir, 'specs')

    local_mirror_dir = os.path.join(ci_project_dir, 'local_mirror')
    build_cache_dir = os.path.join(local_mirror_dir, 'build_cache')

    os.makedirs(job_log_dir)
    os.makedirs(spec_dir)

    job_log_file = os.path.join(job_log_dir, 'cdash_log.txt')

    with open(job_log_file, 'w') as log_fd:
        os.dup2(log_fd.fileno(), sys.stdout.fileno())
        os.dup2(log_fd.fileno(), sys.stderr.fileno())

        import_signing_key(spack_signing_key)

        configure_compilers(spack_compiler_action)

"""
        SPEC_YAML_PATH="${SPEC_DIR}/${SPACK_JOB_SPEC_PKG_NAME}.yaml"
        local spec_names_to_save="${SPACK_JOB_SPEC_PKG_NAME}"

        if [ "${SPACK_ENABLE_CDASH}" == "True" ] ; then
            IFS=';' read -ra DEPS <<< "${SPACK_RELATED_BUILDS}"
            for i in "${DEPS[@]}"; do
                depPkgName="${i}"
                spec_names_to_save="${spec_names_to_save} ${depPkgName}"
                JOB_DEPS_PKG_NAMES+=("${depPkgName}")
            done
        fi

        if [ "${SPACK_COMPILER_ACTION}" == "FIND_ANY" ]; then
            # This corresponds to a bootstrapping phase where we need to
            # rely on any available compiler to build the package (i.e. the
            # compiler needed to be stripped from the spec), and thus we need
            # to concretize the root spec again.
            spack -d buildcache save-yaml --specs "${spec_names_to_save}" --root-spec "${SPACK_ROOT_SPEC}" --yaml-dir "${SPEC_DIR}"
        else
            # in this case, either we're relying on Spack to install missing compiler
            # bootstrapped in a previous phase, or else we only had one phase (like a
            # site which already knows what compilers are available on it's runners),
            # so we don't want to concretize that root spec again.  The reason we need
            # this in the first case (bootstrapped compiler), is that we can't concretize
            # a spec at this point if we're going to ask spack to "install_missing_compilers".
            tmp_dir=$(mktemp -d)
            TMP_YAML_PATH="${tmp_dir}/root.yaml"
            ROOT_SPEC_YAML=$(spack python -c "import base64 ; import zlib ; print(str(zlib.decompress(base64.b64decode('${SPACK_ROOT_SPEC}')).decode('utf-8')))")
            echo "${ROOT_SPEC_YAML}" > "${TMP_YAML_PATH}"
            spack -d buildcache save-yaml --specs "${spec_names_to_save}" --root-spec-yaml "${TMP_YAML_PATH}" --yaml-dir "${SPEC_DIR}"
            rm -rf ${tmp_dir}
        fi
"""
